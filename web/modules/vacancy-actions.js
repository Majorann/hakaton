import { apiFetch } from "./api.js";
import { byId } from "./display.js";
import { dashboard, markReviewed } from "./report-data.js";
import { renderNotice, renderReminder } from "./queue-view.js";
import { renderReport } from "./report-view.js";

export function bindVacancyActions(refresh) {
  byId("mark-reviewed").addEventListener("click", async () => {
    if (!dashboard.detailRow || dashboard.detailRow.entry || dashboard.markingSent) return;
    const source = dashboard.mode;
    const { id, findings, content_hash } = dashboard.detailRow.row;
    if (source === "api") {
      const track = !byId("followup-choice").hidden && byId("track-vacancy").checked;
      const mark = byId("mark-reviewed");
      dashboard.markingSent = true;
      mark.disabled = true;
      dashboard.actionErrors.delete(source);
      try {
        const response = await apiFetch("/api/archive", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id, findings, content_hash, track }),
        });
        const entry = await response.json();
        if (!response.ok) throw new Error(entry.error);
        if (!dashboard.archiveEntries.some(({ key }) => key === entry.key)) dashboard.archiveEntries.push(entry);
        byId("copy-status").textContent = track ? "Вакансия архивирована. Отслеживание включено." : "Вакансия архивирована.";
        await refresh();
      } catch (error) {
        dashboard.actionErrors.set(source, error.message || "Не удалось отметить отправку.");
        renderNotice();
        return;
      } finally {
        dashboard.markingSent = false;
        mark.disabled = false;
      }
    } else {
      markReviewed(source, id, findings);
    }
    if (dashboard.selectedRows.get(source === "api" ? "review" : source) === id) {
      dashboard.selectedRows.delete(source === "api" ? "review" : source);
    }
    renderReport();
    renderReminder();
    if (source === dashboard.mode && (dashboard.mode === "file" || dashboard.archiveView === "review")) {
      byId(dashboard.detailRow ? "detail-title" : "empty-title").focus();
    }
  });
  const complaintDialog = byId("complaint-dialog");
  let complaintTarget = null;
  byId("complain").addEventListener("click", () => {
    if (!dashboard.detailRow) return;
    complaintTarget = { source: dashboard.mode, vacancy: structuredClone(dashboard.detailRow.row) };
    byId("complaint-comment").value = "";
    byId("complaint-message").hidden = true;
    complaintDialog.showModal();
  });
  byId("cancel-complaint").addEventListener("click", () => complaintDialog.close());
  byId("complaint-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!complaintTarget) return;
    const send = byId("send-complaint");
    const message = byId("complaint-message");
    send.disabled = true;
    message.hidden = true;
    try {
      const response = await apiFetch("/api/complaints", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...complaintTarget, comment: byId("complaint-comment").value }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      complaintDialog.close();
      if (dashboard.detailRow?.row.id === complaintTarget.vacancy.id && dashboard.mode === complaintTarget.source) {
        const status = byId("complaint-status");
        status.textContent = "Жалоба сохранена.";
        status.hidden = false;
      }
    } catch (error) {
      message.textContent = error.message || "Не удалось сохранить жалобу. Попробуйте ещё раз.";
      message.hidden = false;
    } finally {
      send.disabled = false;
    }
  });
  byId("download-report").addEventListener("click", () => {
    if (!dashboard.detailRow) return;
    const row = dashboard.detailRow.row;
    const url = URL.createObjectURL(new Blob([JSON.stringify(row, null, 2) + "\n"], { type: "application/json;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `vacancy-${row.id.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 100)}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
