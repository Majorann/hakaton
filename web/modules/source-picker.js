import { byId, modes } from "./display.js";
import { dashboard } from "./report-data.js";

const sourceButtons = [...document.querySelectorAll(".source")];
const sourceTrigger = byId("source-trigger");
const sourceMenu = byId("source-menu");
const sourceCurrentLabel = byId("source-current-label");

export function renderSourcePicker(mode, archiveView) {
  const fromArchive = mode === "api" && archiveView !== "review";
  sourceCurrentLabel.textContent = modes[mode].label;
  sourceTrigger.setAttribute("aria-label", `Источник отчёта: ${modes[mode].label}`);
  byId("choose-file").hidden = mode !== "file";
  byId("choose-file-wrap").hidden = mode !== "file";
  byId("topic-control").hidden = fromArchive;
  for (const button of sourceButtons) {
    const active = button.dataset.mode === mode;
    button.setAttribute("aria-pressed", String(active));
    byId(`filters-${button.dataset.mode}`).hidden = !active || fromArchive;
  }
}

function closeSourceMenu(returnFocus = false) {
  sourceMenu.hidden = true;
  sourceTrigger.setAttribute("aria-expanded", "false");
  if (returnFocus) sourceTrigger.focus();
}

export function bindSourcePicker(switchMode) {
  sourceTrigger.addEventListener("click", () => {
    const expanded = sourceTrigger.getAttribute("aria-expanded") === "true";
    sourceTrigger.setAttribute("aria-expanded", String(!expanded));
    sourceMenu.hidden = expanded;
  });

  const sourceControls = sourceTrigger.parentElement;
  sourceControls.addEventListener("focusout", (event) => {
    if (!sourceControls.contains(event.relatedTarget)) closeSourceMenu();
  });
  for (const button of sourceButtons) {
    button.addEventListener("click", () => {
      if (button.dataset.mode !== dashboard.mode) switchMode(button.dataset.mode);
      closeSourceMenu(true);
    });
  }
  document.addEventListener("pointerdown", (event) => {
    if (!sourceControls.contains(event.target)) closeSourceMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !sourceMenu.hidden) closeSourceMenu(true);
  });
}
