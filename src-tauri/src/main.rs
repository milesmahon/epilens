// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const DEFAULT_PORT: u16 = 8737;
// the server stops by itself this long after the last ping
const IDLE_SECONDS: &str = "60";
const START_TIMEOUT: Duration = Duration::from_secs(60);

// the server we started, if any
struct Server(Mutex<Option<CommandChild>>);

// is epilens answering on this port (not just any program)
fn ping(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    let req =
        format!("GET /api/ping HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = s.read_to_string(&mut reply);
    reply.replace(' ', "").contains("\"app\":\"epilens\"")
}

fn port_in_use(port: u16) -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(500),
    )
    .is_ok()
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(DEFAULT_PORT)
}

// replace the window's page with a message
fn show_error(window: &WebviewWindow, msg: &str) {
    eprintln!("{msg}");
    let text = msg.replace('&', "&amp;").replace('<', "&lt;");
    let html = format!(
        "<!doctype html><meta charset=utf-8><body style=\"margin:0;padding:2em;background:#101418;color:#c8d0d8;\
         font:16px system-ui,sans-serif\"><pre style=\"white-space:pre-wrap;font:inherit\">{text}</pre>"
    );
    let encoded: String = html
        .bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect();
    if let Ok(url) = format!("data:text/html;charset=utf-8,{encoded}").parse() {
        let _ = window.navigate(url);
    }
    let _ = window.show();
}

// start the bundled Python server (the PyInstaller folder shipped as server/); close the app when it quits,
// show its last output if it fails
fn start_server(app: &AppHandle, port: u16) -> Result<(), String> {
    let exe = if cfg!(windows) {
        "epilens-server.exe"
    } else {
        "epilens-server"
    };
    let program = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("server")
        .join(exe);
    let (mut rx, child) = app
        .shell()
        .command(&program)
        .args(["--port", &port.to_string(), "--idle", IDLE_SECONDS])
        .spawn()
        .map_err(|e| e.to_string())?;
    *app.state::<Server>().0.lock().unwrap() = Some(child);

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut last_lines: VecDeque<String> = VecDeque::new();
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    let line = String::from_utf8_lossy(&line).trim_end().to_string();
                    println!("[server] {line}");
                    last_lines.push_back(line);
                    if last_lines.len() > 20 {
                        last_lines.pop_front();
                    }
                }
                CommandEvent::Terminated(p) => {
                    app.state::<Server>().0.lock().unwrap().take();
                    if p.code == Some(0) {
                        app.exit(0); // Quit in the page
                    } else if let Some(w) = app.get_webview_window("main") {
                        let log = Vec::from(last_lines.clone()).join("\n");
                        show_error(
                            &w,
                            &format!(
                                "The epilens server stopped unexpectedly (exit code {:?}). \
                                 Close this window and open epilens again.\n\n{log}",
                                p.code
                            ),
                        );
                    }
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            // use an epilens that is already running (e.g. `uv run epilens`), otherwise start ours
            let (port, started) = if ping(DEFAULT_PORT) {
                (DEFAULT_PORT, None)
            } else {
                let port = if port_in_use(DEFAULT_PORT) {
                    free_port()
                } else {
                    DEFAULT_PORT
                };
                (port, Some(start_server(&handle, port)))
            };

            // wait for the server, open its page, then keep pinging so it stays up while the app is open
            thread::spawn(move || {
                let Some(window) = handle.get_webview_window("main") else {
                    return;
                };
                if let Some(Err(e)) = &started {
                    return show_error(
                        &window,
                        &format!("Couldn't start the epilens server:\n{e}"),
                    );
                }
                let begin = Instant::now();
                while !ping(port) {
                    if started.is_some() && handle.state::<Server>().0.lock().unwrap().is_none() {
                        return; // it exited; its own message is showing
                    }
                    if begin.elapsed() > START_TIMEOUT {
                        return show_error(
                            &window,
                            &format!(
                                "The epilens server didn't answer within {} seconds. \
                                 Close this window and open epilens again.",
                                START_TIMEOUT.as_secs()
                            ),
                        );
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                if let Ok(url) = format!("http://127.0.0.1:{port}/").parse() {
                    let _ = window.navigate(url);
                }
                let _ = window.show();
                let _ = window.set_focus();
                loop {
                    thread::sleep(Duration::from_secs(5));
                    ping(port);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building epilens desktop application");

    // stop our server however the app closes (last window, Cmd+Q)
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = app.state::<Server>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
