// The settings menu (hardware, import and quit), dropping files on the page, and Export CSV
import { $, h } from "./dom.js";

export function makeMenu({ plans }) {
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

  const dropHint = h(
    "div",
    { class: "drop", hidden: "" },
    "Drop CSV or Excel files to open each as a new plan",
  );
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
    const files = [...e.dataTransfer.files];
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

  return { render };
}
