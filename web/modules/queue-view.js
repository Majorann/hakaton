import { byId, clone, countFormat, modes, vacancyTitle } from "./display.js";
import { dashboard, reviewRows } from "./report-data.js";

let queueKey = "";
function renderQueue() {
  const { state, mode } = dashboard;
  const active = state?.active;
  const job = state?.jobs[active ?? mode];
  const rows = job?.vacancies ?? [];
  byId("queue-empty").hidden = Boolean(rows.length);
  const key = rows.length ? JSON.stringify([active, rows, job.current, job.processing, job.stage]) : "";
  if (key === queueKey) return;
  queueKey = key;
  const items = document.createDocumentFragment();
  const outcomes = {
    clean: "Без замечаний", archived: "Ранее в архиве", known: "Уже в отчёте", new: "Новая с замечаниями",
    failed: "Не проверена",
  };
  rows.forEach((row, index) => {
    const item = document.createElement("li");
    const title = document.createElement("span");
    const status = document.createElement("strong");
    const processing = row.id === job.processing;
    const checked = job.stage !== "loading" && (row.index ?? index) < job.current;
    title.textContent = vacancyTitle(row, state);
    status.dataset.status = row.outcome === "failed" ? "failed" :
      processing ? "processing" : checked ? "checked" : "waiting";
    const label = document.createElement("span");
    label.textContent = outcomes[row.outcome] ?? (processing ? "В обработке" : checked ? "Проверена" : "В ожидании");
    status.title = row.error || (row.cached ? `${label.textContent} · Из кеша` : label.textContent);
    if (!row.outcome) label.className = "visually-hidden";
    if (row.outcome !== "failed") status.append(clone(processing ? "queue-processing" : checked ? "queue-check" : "queue-spinner"));
    status.append(label);
    item.append(title, status);
    items.append(item);
  });
  byId("queue-list").replaceChildren(items);
}

export function renderReminder() {
  const { state, mode, starting, reportCache } = dashboard;
  const reminder = byId("stale-reminder");
  const completed = Date.parse(reportCache.get("api")?.data.completed_at) / 1000;
  const updated = Number.isFinite(completed) ? completed : state?.portal_updated;
  const hours = (Date.now() / 1000 - updated) / 3600;
  const outdated = updated && state?.reminder_hours && hours >= state.reminder_hours;
  const pending = outdated && reviewRows(reportCache.get("api")?.data.vacancies ?? [], "api").length;
  reminder.hidden = mode !== "api" || Boolean(state?.active || starting) || !outdated || !pending;
  if (!reminder.hidden) {
    const relative = new Intl.RelativeTimeFormat("ru", { numeric: "always" });
    const age = hours >= 48 ? relative.format(-Math.floor(hours / 24), "day")
      : relative.format(-Math.floor(hours), "hour");
    reminder.textContent = `Рабочий отчёт обновлялся ${age}. В очереди ещё есть вакансии для разбора.`;
  }
}

export function renderNotice() {
  const { state, mode, starting, connectionError, changingAuto, actionErrors, reportErrors, reportCache } = dashboard;
  renderQueue();
  renderReminder();
  const busy = starting || state?.active;
  const activeJob = state?.jobs[state.active];
  const auto = byId("auto-check");
  const autoEnabled = Boolean(state?.active === "file" ? activeJob.enabled : state?.auto_refill);
  const unavailable = Boolean(busy || state?.followup_blocks_main || state?.editor_check) || !state || Boolean(connectionError);
  auto.setAttribute("aria-checked", String(autoEnabled));
  byId("run-api").disabled = unavailable || autoEnabled;
  byId("choose-file").disabled = unavailable;
  auto.disabled = !state || Boolean(connectionError) || Boolean(starting || state?.followup_blocks_main || state?.editor_check) || changingAuto;
  const queueControl = byId("queue-control");
  const notice = byId("notice");
  const job = state?.jobs[mode];
  const summary = !busy && job?.current > 0 && job.new_with_findings !== undefined
    ? `Проверено ${countFormat.format(job.current - (job.failed ?? 0))} · из кеша ${countFormat.format(job.cached)} · новых с замечаниями ${countFormat.format(job.new_with_findings)}.` +
      (job.failed ? ` Не проверено из-за ошибок: ${countFormat.format(job.failed)}.` : "")
    : "";
  queueControl.hidden = !busy && !summary;
  queueControl.toggleAttribute("data-idle", !busy);
  if (queueControl.hidden) closeQueue();
  const queueTitle = busy ? "Текущая обработка" : "Последняя обработка";
  byId("queue-title").textContent = queueTitle;
  byId("queue-trigger").setAttribute("aria-label", queueTitle);
  const old = state?.revisions[mode] ? " Доступен предыдущий успешный отчёт." : "";
  let message = connectionError || actionErrors.get(mode) || reportErrors.get(mode) || "";
  let isError = Boolean(message);
  if (!message && job?.status === "error" && busy !== mode) {
    message = job.error + old;
    if (mode === "api" && state?.auto_refill && state.refill_at) message += " Поиск повторится автоматически.";
    isError = true;
  }
  const archiveChecking = !isError && state?.followup_running;
  if (!message && busy) {
    const label = modes[busy].label;
    if (activeJob?.status !== "running") {
      message = `${label} · Запускаем проверку…`;
    } else if (activeJob.paused) {
      message = `${label} · Автопоток на паузе`;
    } else if (!activeJob.enabled && activeJob.stage !== "saving") {
      message = `${label} · Завершает текущую проверку…`;
    } else if (activeJob.stage === "checking") {
      const total = activeJob.total === null ? "" : ` из ${activeJob.total}`;
      message = `${label} · Проверено ${activeJob.current}${total}`;
    } else if (activeJob.stage === "saving") {
      message = `${label} · Сохраняем отчёт…`;
    } else {
      const loaded = activeJob.current ? ` Получено ${activeJob.current}.` : "";
      message = busy === "api" ? `Загружаем вакансии с портала…${loaded}` : "Читаем локальный JSON…";
    }
  } else if (!message && state?.editor_check) {
    message = "Qwen перепроверяет карточку по отметке редактора…";
  } else if (!message && !archiveChecking && mode === "api" && (state?.auto_refill || job?.status === "success")) {
    const rows = reportCache.get("api")?.data.vacancies ?? [];
    const count = Math.min(state.queue_limit, reviewRows(rows, "api").length);
    message = `В очереди на разбор ${countFormat.format(count)} из ${countFormat.format(state.queue_limit)}.`;
    if (state.auto_refill && state.refill_at) message += " Автопоток продолжит поиск.";
    else if (!state.auto_refill && job?.shortage && count < state.queue_limit) message += " В выборке не хватило подходящих вакансий.";
  } else if (!message && !state) {
    message = "Проверяем доступность отчётов…";
  }
  if (archiveChecking) message = [message, "Проверяем исправления в архиве…"].filter(Boolean).join(" · ");
  if (summary && !isError) message = [message, summary].filter(Boolean).join(" ");
  const savedErrors = reportCache.get(mode)?.data.errors?.length;
  if (!busy && !summary && !isError && savedErrors) {
    message = [message, `В сохранённом отчёте не проверено из-за ошибок: ${countFormat.format(savedErrors)}.`]
      .filter(Boolean).join(" ");
  }
  if (notice.textContent !== message) notice.textContent = message;
  byId("queue-progress").textContent = isError ? [summary, message].filter(Boolean).join(" ") : message;
  notice.classList.toggle("visually-hidden", Boolean(busy && !isError));
  notice.dataset.tone = isError ? "error" : "info";
  notice.hidden = !message;
}

const queueControl = byId("queue-control");
const queueTrigger = byId("queue-trigger");
const queuePanel = byId("live-queue");
let queuePinned = false;
function setQueueOpen(open) {
  queuePanel.hidden = !open;
  queueTrigger.setAttribute("aria-expanded", String(open));
}
function closeQueue() {
  queuePinned = false;
  setQueueOpen(false);
}
queueControl.addEventListener("pointerenter", (event) => {
  if (event.pointerType !== "touch") setQueueOpen(true);
});
queueControl.addEventListener("pointerleave", () => {
  if (!queuePinned && !queueControl.contains(document.activeElement)) setQueueOpen(false);
});
queueControl.addEventListener("focusin", () => setQueueOpen(true));
queueControl.addEventListener("focusout", (event) => {
  if (!queuePinned && !queueControl.contains(event.relatedTarget)) setQueueOpen(false);
});
queueTrigger.addEventListener("click", () => {
  queuePinned = !queuePinned;
  setQueueOpen(queuePinned);
});
document.addEventListener("pointerdown", (event) => {
  if (!queueControl.contains(event.target)) closeQueue();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !queuePanel.hidden) {
    closeQueue();
    queueTrigger.focus();
  }
});
