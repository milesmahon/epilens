// The settings menu (hardware, import and quit), dropping files on the page, and Export CSV
import { $, h } from "./dom.js";

const DROP_TEXT =
  "Drop CSV or Excel files to add electrodes, or drop a reconstruction folder";

// LeGUI files to send to the server, which checks that none are missing
const PATIENT_FILE =
  /^(mr|y_mr|surfaces|mrgray|mrwhite|acpcmm|ct)\.(nii|nii\.gz|mat)$/i;
const fileKey = (name) =>
  name.toLowerCase().replace(/\.(nii\.gz|nii|mat)$/, "");

// Every file in the dropped files and folders, looking inside folders
async function filesIn(entries) {
  const out = [];
  for (const en of entries) {
    if (en.isFile) {
      const f = await new Promise((ok, bad) => en.file(ok, bad));
      f.path = en.fullPath; // where it was in the dropped folder, to name duplicates
      out.push(f);
    } else if (en.isDirectory) {
      const reader = en.createReader();
      for (;;) {
        // readEntries returns a batch at a time, and an empty batch at the end
        const batch = await new Promise((ok, bad) =>
          reader.readEntries(ok, bad),
        );
        if (!batch.length) break;
        out.push(...(await filesIn(batch)));
      }
    }
  }
  return out;
}

export function makeMenu({ plans, anatomy }) {
  const menu = $("menu");
  let applyTo = "all";
  const settingRow = (label, key, step) => {
    const inp = h("input", { type: "number", name: key, step, min: 0.1 });
    const sel = plans.selectedElectrode;
    inp.value =
      key === "spacing_mm" && applyTo === "selected" && sel?.spacing_mm
        ? sel.spacing_mm
        : plans.current.settings[key];
    let last = inp.value; // so Enter and the following change event don't both commit
    const commit = () => {
      const v = parseFloat(inp.value);
      if (!(v > 0) || inp.value === last) return;
      last = inp.value;
      if (
        applyTo === "selected" &&
        key === "spacing_mm" &&
        plans.selectedElectrode
      ) {
        plans.update(plans.selected, { spacing_mm: v });
      } else {
        if (key === "spacing_mm")
          for (const e of plans.current.electrodes) e.spacing_mm = null;
        plans.updateSettings({ [key]: v });
      }
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commit();
      }
    });
    inp.addEventListener("change", commit);
    return h("div", { class: "row" }, h("span", {}, label), inp);
  };

  async function importFile(file) {
    const name = encodeURIComponent(
      file.name
        .replace(/\.(csv|txt|xlsx)$/i, "")
        .replace(/_\d{4}-\d{2}-\d{2}$/, ""), // remove the date that export adds to the file name
    );
    const r = await fetch(`api/import?name=${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" }, // CSV or .xlsx
      body: await file.arrayBuffer(),
    }).catch(() => null);
    const body = await r?.json().catch(() => ({}));
    if (!r?.ok)
      return alert(
        `Could not import ${file.name}:\n${body?.error || "can't reach epilens"}`,
      );
    const { notes, ...plan } = body;
    plans.add(null, plan);
    if (notes?.length) alert(`Imported ${file.name}.\n\n${notes.join("\n\n")}`);
  }

  const dropHint = h("div", { class: "drop", hidden: "" }, DROP_TEXT);
  document.body.append(dropHint);
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  let depth = 0; // dragenter and dragleave fire for each child element, so count them
  document.addEventListener("dragenter", (e) => {
    if (hasFiles(e)) {
      depth++;
      dropHint.hidden = false;
    }
  });
  document.addEventListener("dragleave", (e) => {
    if (hasFiles(e) && --depth <= 0) {
      depth = 0;
      dropHint.hidden = true;
    }
  });
  document.addEventListener("dragover", (e) => {
    if (hasFiles(e)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  });
  document.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); // otherwise the browser opens the file
    depth = 0;
    dropHint.hidden = true;
    // Read everything from the drop before the first await: the browser empties dataTransfer afterwards
    const files = [...e.dataTransfer.files];
    const entries = [...e.dataTransfer.items]
      .map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry())
      .filter(Boolean);
    if (entries.some((en) => en.isDirectory || PATIENT_FILE.test(en.name))) {
      const all = await filesIn(entries);
      if (all.some((f) => PATIENT_FILE.test(f.name))) return attachPatient(all);
    }
    const csvs = files.filter(
      (f) => /\.(csv|txt|xlsx|xls)$/i.test(f.name) || f.type === "text/csv",
    );
    if (csvs.length < files.length) {
      alert(
        `Only CSV and Excel files can be opened here (skipped: ${files
          .filter((f) => !csvs.includes(f))
          .map((f) => f.name)
          .join(", ")}).`,
      );
    }
    for (const f of csvs) await importFile(f);
  });

  // A patient's LeGUI files: hand them to epilens on this computer, which moves this plan onto the patient's brain
  async function attachPatient(dropped) {
    const plan = plans.current;
    const files = dropped.filter((f) => PATIENT_FILE.test(f.name));
    const byKey = {};
    for (const f of files) (byKey[fileKey(f.name)] ||= []).push(f);
    const twice = Object.values(byKey).filter((fs) => fs.length > 1);
    if (twice.length)
      return alert(
        `These files are in the drop more than once, so it isn't clear which patient they belong to:\n${twice
          .flat()
          .map((f) => f.path || f.name)
          .join("\n")}\n\nDrop just the patient's Registered folder.`,
      );
    let name = null;
    if (/^Plan \d+$/.test(plan.name)) {
      while (!name) {
        const typed = prompt(
          "Name this plan (for example the patient's name or ID):",
        );
        if (typed === null) return; // cancelled, so nothing happens
        name = typed.trim();
      }
    }
    const ask =
      plan.space !== "patient"
        ? "Move this plan onto the patient's brain? Electrodes will be converted to the patient's AC-PC coordinates."
        : anatomy.missing
          ? null // after a restart: just load the brain back
          : "This plan is already on a patient's brain. Load this folder as its brain instead? Coordinates won't change, so it must be the same patient.";
    if (ask && !confirm(ask)) return;
    if (name) plans.rename(plan.id, name);
    if (!(await plans.saveNow(plan)))
      return alert(
        "Couldn't save this plan first, so the patient wasn't loaded.",
      );
    // Hold all edits until the server has converted the plan: anything saved meanwhile would be overwritten, or undo it
    plans.paused = true;
    for (const t of Object.values(plans._timers)) clearTimeout(t);
    const status = (msg) => {
      dropHint.textContent = msg;
      dropHint.classList.add("busy"); // covers the page so nothing can be changed
      document.activeElement?.blur(); // and nothing can be typed
      dropHint.hidden = false;
    };
    try {
      // One file per request, sent whole every attempt. The first one tells the server to forget earlier attempts
      for (const [k, f] of files.entries()) {
        status(`Reading ${f.name} (${k + 1}/${files.length})…`);
        const r = await fetch(
          `api/plans/${plan.id}/patient/files/${encodeURIComponent(f.name)}${k ? "" : "?first=1"}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: f,
          },
        );
        if (!r.ok)
          throw new Error(
            (await r.json().catch(() => ({}))).error || `HTTP ${r.status}`,
          );
      }
      status("Building the patient's brain…");
      const r = await fetch(`api/plans/${plan.id}/patient`, { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
      plans.replace(body.plan);
      if (body.warnings?.length) alert(body.warnings.join("\n"));
    } catch (err) {
      plans.paused = false;
      return alert(`Couldn't load the patient:\n${err.message || err}`);
    } finally {
      dropHint.hidden = true;
      dropHint.classList.remove("busy");
      dropHint.textContent = DROP_TEXT;
    }
    try {
      localStorage.setItem("epilens.currentPlan", plan.id);
    } catch (e) {
      /* storage blocked */
    }
    location.reload(); // the page is built for one brain, so reload onto the patient's
  }

  async function quit() {
    if (!confirm("Stop epilens? Plans are saved.")) return;
    await fetch("api/quit", { method: "POST" }).catch(() => {});
    document.body.append(h("div", { class: "stopped" }, "epilens stopped."));
  }

  // file picker in the menu, used for the Import button
  const file = h("input", {
    type: "file",
    accept: ".csv,text/csv,.xlsx,.xls",
    style: "display:none",
    onchange: (e) => {
      if (e.target.files[0]) importFile(e.target.files[0]);
      e.target.value = "";
    },
  });
  menu.append(file);
  $("importBtn").addEventListener("click", () => file.click());

  function render() {
    if (menu.hidden) return;
    const applySel = h(
      "select",
      {
        name: "applyTo",
        onchange: (e) => {
          applyTo = e.target.value;
          render();
        },
      },
      h("option", { value: "all" }, "All electrodes in this plan"),
      h("option", { value: "selected" }, "Selected electrode only"),
    );
    applySel.value = applyTo;
    menu.replaceChildren(
      h("h3", {}, "Electrode hardware"),
      h("div", { class: "row" }, h("span", {}, "Apply to"), applySel),
      settingRow("Spacing (mm)", "spacing_mm", 0.5),
      settingRow("Contact length (mm)", "contact_len_mm", 0.01),
      settingRow("Diameter (mm)", "diameter_mm", 0.01),
      h(
        "div",
        { class: "note" },
        "Plans autosave to Documents/epilens/plans on this computer.",
      ),
      h(
        "div",
        { class: "note" },
        "Not a medical device. Check every plan against the patient's own imaging.",
      ),
      h(
        "div",
        { class: "buttons" },
        h("button", { name: "quit", onclick: quit }, "Quit epilens"),
      ),
      file,
    );
  }
  const menuBtn = $("menuBtn");
  const setOpen = (open) => {
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
    render();
  };
  menuBtn.addEventListener("click", () => setOpen(menu.hidden));
  $("hardware").addEventListener("click", () => setOpen(true));
  // close it by clicking elsewhere or with Escape
  document.addEventListener("pointerdown", (e) => {
    if (
      !menu.hidden &&
      !menu.contains(e.target) &&
      e.target !== menuBtn &&
      e.target.id !== "hardware"
    )
      setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) setOpen(false);
  });

  // save first, because the server exports the saved plan
  $("exportBtn").addEventListener("click", async () => {
    if (!(await plans.saveNow(plans.current)))
      return alert(`Export failed: ${plans.saveError || "this tab is paused"}`);
    h("a", { href: `api/export/${plans.current.id}`, download: "" }).click();
  });

  return { render, attachPatient };
}
