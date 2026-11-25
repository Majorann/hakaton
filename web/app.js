import { apiFetch } from "./modules/api.js";
import { byId, modes } from "./modules/display.js";
import { renderNotice } from "./modules/queue-view.js";
import { dashboard } from "./modules/report-data.js";
import { renderReport } from "./modules/report-view.js";
import { bindSettingsDialog } from "./modules/settings-dialog.js";
import { bindSourcePicker } from "./modules/source-picker.js";
import { bindVacancyActions } from "./modules/vacancy-actions.js";

let refreshSequence = 0;
let reportSequence = 0;

async function updateReport() {
  const current = dashboard.mode;
  const revision = dashboard.state?.revisions[current];
  const sequence = ++reportSequence;
  if (!revision) {
    if (dashboard.reportCache.delete(current)) renderReport();
    return;
  }
  if (dashboard.reportCache.get(current)?.revision === revision) return;
  try {
    const response = await apiFetch(`/api/reports/${current}`);
    if (!response.ok) throw new Error();
    const raw = await response.text();
    const data = JSON.parse(raw);
    if (!Array.isArray(data?.vacancies) ||
        data.vacancies.some((row) => typeof row?.id !== "string" || !Array.isArray(row.findings))) {
      throw new Error();
    }
    if (sequence !== reportSequence) return;
    dashboard.reportErrors.delete(current);
    dashboard.reportCache.set(current, { data, raw, revision });
  } catch {
    if (sequence !== reportSequence) return;
    dashboard.reportErrors.set(current, "Не удалось прочитать сохранённый отчёт.");
  }
  renderReport();
  renderNotice();
}

async function refresh() {
  const sequence = ++refreshSequence;
  try {
    const response = await apiFetch("/api/state");
    if (response.status === 401) throw new Error("Откройте ссылку доступа из консоли, где запущен сервер.");
    if (!response.ok) throw new Error();
    const next = await response.json();
    if (sequence !== refreshSequence) return;
    const first = !dashboard.state;
    const wasActive = dashboard.state?.active === dashboard.mode;
    const previousCount = dashboard.state?.jobs[dashboard.mode]?.results?.length;
    const preferencesChanged = next.followup_enabled !== dashboard.state?.followup_enabled ||
      next.followup_blocks_main !== dashboard.state?.followup_blocks_main ||
      next.editor_check !== dashboard.state?.editor_check ||
      next.followup_minutes !== dashboard.state?.followup_minutes ||
      next.queue_limit !== dashboard.state?.queue_limit;
    const archiveChanged = next.archive_revision !== dashboard.archiveRevision;
    if (archiveChanged) {
      const archive = await apiFetch("/api/archive");
      if (!archive.ok) throw new Error("Не удалось загрузить архив заявок.");
      const data = await archive.json();
      if (sequence !== refreshSequence) return;
      dashboard.archiveEntries = data.entries;
      dashboard.archiveRevision = data.revision;
    }
    dashboard.state = next;
    dashboard.connectionError = "";
    if (first) {
      dashboard.mode = next.active ?? Object.keys(modes).find((key) => next.revisions[key]) ?? "api";
    }
    if (first || archiveChanged || preferencesChanged) renderReport();
    renderNotice();
    await updateReport();
    if (wasActive !== (dashboard.state?.active === dashboard.mode) ||
        (dashboard.state?.active === dashboard.mode && previousCount !== dashboard.state.jobs[dashboard.mode].results.length)) {
      renderReport();
    }
  } catch (error) {
    if (sequence !== refreshSequence) return;
    dashboard.connectionError = error instanceof TypeError || !error.message
      ? "Нет связи с локальной программой. Проверьте, запущен ли сервер."
      : error.message;
    renderNotice();
  }
}

function switchMode(nextMode) {
  dashboard.mode = nextMode;
  renderReport();
  renderNotice();
  void updateReport();
}

async function run(nextMode, upload, autoflow = false) {
  if (dashboard.starting || dashboard.state?.active || dashboard.state?.followup_blocks_main || dashboard.state?.editor_check) return;
  dashboard.actionErrors.delete(nextMode);
  dashboard.starting = nextMode;
  switchMode(nextMode);
  try {
    const response = await apiFetch(`/api/runs/${nextMode}${autoflow ? "/auto" : ""}`, {
      method: "POST",
      ...(upload ? { headers: { "Content-Type": "application/json" }, body: upload } : {}),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error);
    }
    await refresh();
  } catch (error) {
    dashboard.actionErrors.set(nextMode, error.message || "Не удалось запустить проверку.");
  } finally {
    dashboard.starting = null;
    renderNotice();
  }
}

bindSourcePicker(switchMode);
byId("run-api").addEventListener("click", () => void run("api"));
byId("auto-check").addEventListener("click", async () => {
  const active = dashboard.state?.active;
  if (!active && !dashboard.state?.auto_refill) {
    void run("api", null, true);
    return;
  }
  dashboard.actionErrors.delete(dashboard.mode);
  dashboard.changingAuto = true;
  renderNotice();
  try {
    const enabled = active === "file" ? dashboard.state.jobs.file.enabled : dashboard.state.auto_refill;
    const action = enabled ? "pause" : "resume";
    const response = await apiFetch(`/api/queue/${action}`, { method: "POST" });
    if (!response.ok && response.status !== 409) throw new Error();
    await refresh();
  } catch {
    dashboard.actionErrors.set(dashboard.mode, "Не удалось переключить автопоток.");
  } finally {
    dashboard.changingAuto = false;
    renderNotice();
  }
});
byId("choose-file").addEventListener("click", () => byId("upload-file").click());
byId("upload-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) void run("file", file);
});
bindVacancyActions(refresh);
bindSettingsDialog(refresh);

async function poll() {
  await refresh();
  setTimeout(poll, dashboard.state?.active || dashboard.starting ? 750 : 2200);
}
void poll();
