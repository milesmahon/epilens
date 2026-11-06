// replace browser alerts
import { h } from "./dom.js";

// show a box; resolves with the button's value, or null for Escape
function open(message, buttons, input = null) {
  return new Promise((resolve) => {
    const box = h(
      "dialog",
      { class: "notice" },
      h("p", {}, message),
      input,
      h(
        "div",
        { class: "buttons" },
        ...buttons.map(([label, value, primary]) =>
          h(
            "button",
            {
              class: primary ? "primary" : "",
              onclick: () => done(value),
            },
            label,
          ),
        ),
      ),
    );
    const done = (value) => {
      box.close();
      box.remove();
      resolve(value);
    };
    box.addEventListener("cancel", (e) => {
      e.preventDefault();
      done(null);
    });
    input?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        done(buttons.find((b) => b[2])[1]);
      }
    });
    document.body.append(box);
    box.showModal();
    (input || box.querySelector("button.primary"))?.focus();
  });
}

export const notify = (message) => open(message, [["OK", true, true]]);

// true for OK, false for Cancel or Escape
export const ask = async (message, ok = "OK") =>
  !!(await open(message, [
    ["Cancel", false],
    [ok, true, true],
  ]));

// the typed text, or null if cancelled
export async function askText(message) {
  const inp = h("input", { type: "text", spellcheck: "false" });
  const ok = await open(
    message,
    [
      ["Cancel", false],
      ["OK", true, true],
    ],
    inp,
  );
  return ok ? inp.value : null;
}
