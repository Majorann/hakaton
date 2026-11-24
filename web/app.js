"use strict";

const byId = (id) => document.getElementById(id);
const accessKey = "vacancy-access";
let accessToken;
try {
  accessToken = sessionStorage.getItem(accessKey);
} catch {
  // Доступ к странице по ссылке по-прежнему возможен.
}

function readAccessLink() {
  const token = new URLSearchParams(location.hash.slice(1)).get("access");
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) return;
  accessToken = token;
  try {
    sessionStorage.setItem(accessKey, token);
  } catch {
    // Токен остаётся доступен на этой странице.
  }
  history.replaceState(null, "", location.pathname + location.search);
}

readAccessLink();
window.addEventListener("hashchange", readAccessLink);

function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(path, { ...options, headers, redirect: "error" });
}
const sourceButtons = [...document.querySelectorAll(".source")];
const sourceTrigger = byId("source-trigger");
const sourceMenu = byId("source-menu");
const sourceCurrentLabel = byId("source-current-label");
const modes = {
  api: { label: "Рабочий отчёт", filename: "report.json", empty: "Загрузите вакансии с портала «Работа России»." },
  file: { label: "Локальный JSON", filename: "verification-report.json", empty: "Выберите сохранённый ответ API в формате JSON." },
};
const topics = {
  title: "Должность", duties: "Обязанности", requirements: "Требования", pay: "Оплата",
  schedule: "График", location: "Место работы", employment: "Занятость",
};
const kinds = { missing: "Пропуск", ambiguous: "Неоднозначность", contradiction: "Противоречие" };
const fieldLabels = {
  "job-name": "Должность", duty: "Обязанности", description: "Описание вакансии",
  "job-description": "Описание работы", requirements: "Требования", qualification: "Квалификация",
  skills: "Навыки", salary: "Зарплата", salary_min: "Минимальная зарплата",
  salary_max: "Максимальная зарплата", currency: "Валюта", schedule: "График работы",
  scheduleTypeComment: "Описание графика", shift: "Смена", employment: "Тип занятости",
  "requirement.education": "Образование", "requirement.qualification": "Требуемая квалификация",
  "requirement.experience": "Опыт работы", "term.text": "Условия работы",
  "addresses.address": "Адрес работы", "addresses.address.location": "Адрес работы",
};

function fieldLabel(field) {
  return fieldLabels[field.replace(/\[\d+\]/g, "")] ?? "Поле вакансии";
}

const reportCache = new Map();
const selectedRows = new Map();
const reportErrors = new Map();
const actionErrors = new Map();
const reviewedKey = "vacancy-reviewed-v1";
let reviewed = new Map();
try {
  reviewed = new Map(JSON.parse(localStorage.getItem(reviewedKey) || "[]"));
} catch {
  // Очередь на этой странице работает и без доступа к хранилищу.
}
const countFormat = new Intl.NumberFormat("ru-RU");
const plurals = new Intl.PluralRules("ru-RU");
let mode = "api";
let state = null;
let starting = null;
let connectionError = "";
let refreshSequence = 0;
let reportSequence = 0;
let changingAuto = false;
let shownDetail = "";
let detailRow;
let archiveView = "review";
let archiveRevision = null;
let archiveEntries = [];
let markingSent = false;
const viewSelect = byId("vacancy-view");
const viewOptions = [...viewSelect.options].map((option) => [option, option.textContent]);
const pendingStatuses = new Set(["waiting", "partial"]);
const archiveLabels = {
  waiting: "Ожидает исправления", partial: "Частично исправлена", fixed: "Исправлена",
  archived: "В архиве без отслеживания", unavailable: "Недоступна на портале",
};
const archiveDate = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
for (const [value, label] of Object.entries(topics)) {
  for (const select of document.querySelectorAll(".topic")) select.add(new Option(label, value));
}

function pluralCount(count, one, few, many) {
  const form = { one, few, many }[plurals.select(count)] ?? many;
  return `${countFormat.format(count)} ${form}`;
}

function vacancyTitle(row) {
  const reference = state?.reference_titles ?? {};
  return row.title || (Object.hasOwn(reference, row.id) ? reference[row.id] : `Вакансия ${row.id.slice(0, 8)}`);
}

function vacancyUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && ["trudvsem.ru", "www.trudvsem.ru"].includes(url.hostname)
      && !url.username && !url.password && !url.port ? url.href : "";
  } catch {
    return "";
  }
}

function clone(id) {
  return byId(id).content.firstElementChild.cloneNode(true);
}

let queueKey = "";
function renderQueue() {
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
  };
  rows.forEach((row, index) => {
    const item = document.createElement("li");
    const title = document.createElement("span");
    const status = document.createElement("strong");
    const processing = row.id === job.processing;
    const checked = job.stage !== "loading" && (row.index ?? index) < job.current;
    title.textContent = vacancyTitle(row);
    status.dataset.status = processing ? "processing" : checked ? "checked" : "waiting";
    const label = document.createElement("span");
    label.textContent = outcomes[row.outcome] ?? (processing ? "В обработке" : checked ? "Проверена" : "В ожидании");
    status.title = row.cached ? `${label.textContent} · Из кеша` : label.textContent;
    if (!row.outcome) label.className = "visually-hidden";
    status.append(clone(processing ? "queue-processing" : checked ? "queue-check" : "queue-spinner"), label);
    item.append(title, status);
    items.append(item);
  });
  byId("queue-list").replaceChildren(items);
}

function renderReminder() {
  const reminder = byId("stale-reminder");
  const hours = (Date.now() / 1000 - state?.portal_updated) / 3600;
  const outdated = state?.portal_updated && state.reminder_hours && hours >= state.reminder_hours;
  const pending = outdated && reviewRows(reportCache.get("api")?.data.vacancies ?? [], "api").length;
  reminder.hidden = mode !== "api" || Boolean(state?.active || starting) || !outdated || !pending;
  if (!reminder.hidden) {
    const relative = new Intl.RelativeTimeFormat("ru", { numeric: "always" });
    const age = hours >= 48 ? relative.format(-Math.floor(hours / 24), "day")
      : relative.format(-Math.floor(hours), "hour");
    reminder.textContent = `Рабочий отчёт обновлялся ${age}. В очереди ещё есть вакансии для разбора.`;
  }
}

function renderNotice() {
  renderQueue();
  renderReminder();
  const busy = starting || state?.active;
  const activeJob = state?.jobs[state.active];
  const auto = byId("auto-check");
  const autoEnabled = Boolean(state?.active === "file" ? activeJob.enabled : state?.auto_refill);
  const unavailable = Boolean(busy || state?.followup_running) || !state || Boolean(connectionError);
  auto.setAttribute("aria-checked", String(autoEnabled));
  byId("run-api").disabled = unavailable || autoEnabled;
  byId("choose-file").disabled = unavailable;
  auto.disabled = !state || Boolean(connectionError) || Boolean(starting || state?.followup_running) || changingAuto;
  const queueControl = byId("queue-control");
  const notice = byId("notice");
  const job = state?.jobs[mode];
  const summary = !busy && job?.current > 0 && job.new_with_findings !== undefined
    ? `Проверено ${countFormat.format(job.current)} · из кеша ${countFormat.format(job.cached)} · новых с замечаниями ${countFormat.format(job.new_with_findings)}.`
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
  } else if (!message && state?.followup_running) {
    message = "Проверяем исправления в архиве…";
  } else if (!message && mode === "api" && (state?.auto_refill || job?.status === "success")) {
    const rows = reportCache.get("api")?.data.vacancies ?? [];
    const count = Math.min(state.queue_limit, reviewRows(rows, "api").length);
    message = `В очереди на разбор ${countFormat.format(count)} из ${countFormat.format(state.queue_limit)}.`;
    if (state.auto_refill && state.refill_at) message += " Автопоток продолжит поиск.";
    else if (!state.auto_refill && job?.shortage && count < state.queue_limit) message += " В выборке не хватило подходящих вакансий.";
  } else if (!message && !state) {
    message = "Проверяем доступность отчётов…";
  }
  if (summary && !isError) message = [message, summary].filter(Boolean).join(" ");
  if (notice.textContent !== message) notice.textContent = message;
  byId("queue-progress").textContent = isError ? [summary, message].filter(Boolean).join(" ") : message;
  notice.classList.toggle("visually-hidden", Boolean(busy && !isError));
  notice.dataset.tone = isError ? "error" : "info";
  notice.hidden = !message;
}

function renderArchiveDetail(entry, preserveHistory) {
  const panel = byId("followup-detail");
  const historyOpen = preserveHistory && panel.querySelector("details")?.open;
  panel.hidden = !entry;
  panel.replaceChildren();
  if (!entry) return;

  const status = document.createElement("strong");
  status.textContent = archiveLabels[entry.status];
  const dates = document.createElement("p");
  const format = (seconds) => archiveDate.format(seconds * 1000);
  const dateParts = [`Отмечена отправка ${format(entry.sent_at)}`];
  if (entry.checked_at) dateParts.push(`Проверено ${format(entry.checked_at)}`);
  if (entry.unchanged) dateParts.push("Поля для анализа без изменений");
  if (pendingStatuses.has(entry.status)) {
    if (state?.followup_enabled) {
      const interval = state.followup_minutes * 60;
      const next = (entry.attempted_at ?? entry.sent_at) + (entry.error ? Math.min(interval, 900) : interval);
      dateParts.push(next > Date.now() / 1000 ? `Следующая проверка ${format(next)}` : "Проверка ожидается");
    } else {
      dateParts.push("Отслеживание на паузе");
    }
  }
  dates.textContent = dateParts.join(" · ");
  panel.append(status, dates);

  if (entry.error) {
    const error = document.createElement("p");
    error.className = "check-error";
    error.textContent = `Последняя попытка завершилась ошибкой. ${entry.error}`;
    panel.append(error);
  }
  const originalTopics = new Set(entry.original.findings.map((finding) => finding.topic));
  const newTopics = new Set(entry.latest?.findings.map((finding) => finding.topic)
    .filter((topic) => !originalTopics.has(topic)));
  if (newTopics.size) {
    const note = document.createElement("p");
    note.textContent = `Новые замечания в последней проверке — ${[...newTopics].map((topic) => topics[topic] ?? topic).join(", ")}.`;
    panel.append(note);
  }
  if (entry.history.length) {
    const history = document.createElement("details");
    history.open = Boolean(historyOpen);
    const heading = document.createElement("summary");
    heading.textContent = "История повторных проверок";
    history.append(heading);
    const events = document.createElement("ul");
    for (const event of [...entry.history].reverse()) {
      const item = document.createElement("li");
      item.textContent = `${format(event.at)} · ${archiveLabels[event.status]}`;
      if (event.model) item.textContent += ` · ${event.model}`;
      if (event.findings?.length) {
        const findings = document.createElement("ul");
        for (const finding of event.findings) {
          const note = document.createElement("li");
          note.textContent = `${topics[finding.topic] ?? finding.topic} — ${finding.problem}`;
          findings.append(note);
        }
        item.append(findings);
      }
      events.append(item);
    }
    history.append(events);
    panel.append(history);
  }
}

function renderHighlightedText(element, text, fragments) {
  const highlights = fragments.filter((fragment) => typeof fragment === "string" && fragment)
    .map((fragment) => ({ fragment, start: text.indexOf(fragment) }))
    .filter(({ start }) => start >= 0)
    .sort((a, b) => a.start - b.start || b.fragment.length - a.fragment.length);
  element.replaceChildren();
  let cursor = 0;
  for (const { start, fragment } of highlights) {
    if (start < cursor) continue;
    const mark = document.createElement("mark");
    mark.textContent = fragment;
    element.append(text.slice(cursor, start), mark);
    cursor = start + fragment.length;
  }
  element.append(text.slice(cursor));
}

function renderDetail(row, entry) {
  const key = JSON.stringify([mode, row, entry, state?.followup_enabled, state?.followup_minutes]);
  if (key === shownDetail) return;
  const preserveHistory = entry?.key && detailRow?.entry?.key === entry.key;
  shownDetail = key;
  detailRow = row ? { row, entry } : null;
  byId("complaint-status").hidden = true;
  const mark = byId("mark-reviewed");
  mark.hidden = !row || Boolean(entry);
  mark.disabled = markingSent;
  mark.title = mode === "api" ? "Рекомендации отправлены" : "Разобрано";
  mark.setAttribute("aria-label", mark.title);
  const trackingOption = mode === "api" && row && !entry && state?.followup_enabled;
  const card = row && vacancyUrl(row.url);
  const cardId = card && /^\/vacancy\/card\/[A-Za-z0-9_-]{1,128}\/([A-Za-z0-9_-]{1,256})\/?$/
    .exec(new URL(card).pathname)?.[1];
  const canTrack = row && /^[A-Za-z0-9_-]{1,256}$/.test(row.id) && (row.company_code || cardId === row.id);
  byId("followup-choice").hidden = !trackingOption || !canTrack;
  byId("followup-unavailable").hidden = !trackingOption || Boolean(canTrack);
  byId("track-vacancy").checked = true;
  renderArchiveDetail(entry, preserveHistory);
  const heading = byId("detail-title");
  const link = byId("detail-link");
  const url = row ? vacancyUrl(row.url) : "";
  heading.textContent = row ? vacancyTitle(row) : "Замечания";
  byId("detail-id").textContent = row?.id ?? "";
  link.hidden = !url;
  if (url) link.href = url;
  else link.removeAttribute("href");

  const detail = byId("findings");
  detail.replaceChildren();
  if (!row || !row.findings.length) {
    const note = document.createElement("p");
    note.className = "no-findings";
    note.textContent = row ? "Замечаний нет" : "Выберите вакансию из списка.";
    detail.append(note);
    return;
  }
  for (const finding of row.findings) {
    const item = clone("finding-template");
    item.querySelector("h3").textContent = topics[finding.topic] ?? finding.topic;
    const kind = item.querySelector(".kind");
    kind.textContent = kinds[finding.kind] ?? finding.kind;
    kind.dataset.kind = finding.kind;
    if (entry && (pendingStatuses.has(entry.status) || entry.status === "fixed")) {
      const progress = document.createElement("span");
      const fixed = entry.resolved.includes(finding.topic);
      progress.className = "finding-progress";
      progress.dataset.fixed = String(fixed);
      progress.textContent = fixed ? "Исправлено" : "Ожидает исправления";
      kind.after(progress);
    }
    const problemFragments = [finding.problem_highlight];
    if (finding.kind === "contradiction" && !finding.problem_highlight) {
      for (const quote of finding.evidence) {
        if (quote.highlight || quote.value.length >= 4 && quote.value.length <= 120) {
          problemFragments.push(quote.highlight || quote.value);
        }
      }
    }
    renderHighlightedText(item.querySelector(".problem"), finding.problem, problemFragments);
    const evidenceBlock = item.querySelector(".evidence-block");
    const evidence = item.querySelector(".evidence");
    for (const { field, value, highlight } of finding.evidence) {
      const citation = clone("evidence-template");
      citation.querySelector("code").textContent = fieldLabel(field);
      const quote = citation.querySelector("blockquote");
      quote.dataset.empty = String(!value);
      const fragment = highlight || (finding.kind === "contradiction" && value.length <= 120 ? value : "");
      renderHighlightedText(quote, value || "Значение не указано", [fragment]);
      evidence.append(citation);
    }
    evidenceBlock.hidden = evidence.children.length === 0;
    const draft = finding.recommendation_source !== "rule";
    item.querySelector(".recommendation-label").textContent = draft
      ? "Черновик вопроса · проверьте перед отправкой"
      : "Вопрос работодателю";
    item.querySelector(".recommendation").textContent = finding.recommendation;
    const copy = item.querySelector(".copy");
    const copyLabel = copy.querySelector("span");
    const copyIcon = copy.querySelector("use");
    const copyError = item.querySelector(".copy-error");
    const copyStatus = byId("copy-status");
    let resetCopy;
    const setCopied = (copied) => {
      copy.toggleAttribute("data-copied", copied);
      copyLabel.textContent = copied ? "Скопировано" : draft ? "Скопировать черновик" : "Скопировать вопрос";
      copyIcon.setAttribute("href", copied ? "#check" : "#copy");
    };
    setCopied(false);
    copy.addEventListener("click", async () => {
      copyStatus.textContent = "";
      try {
        await navigator.clipboard.writeText(finding.recommendation);
        clearTimeout(resetCopy);
        copyError.hidden = true;
        setCopied(true);
        copyStatus.textContent = "Вопрос скопирован";
        resetCopy = setTimeout(() => setCopied(false), 1800);
      } catch {
        clearTimeout(resetCopy);
        setCopied(false);
        copyError.hidden = false;
        copyStatus.textContent = "Не удалось скопировать вопрос. Скопируйте текст вручную.";
      }
    });
    detail.append(item);
  }
}

function completedRows(liveRows) {
  const saved = reportCache.get(mode)?.data.vacancies;
  if (!liveRows) return saved;
  if (mode !== "api" || !saved) return liveRows;
  const byVacancy = new Map(saved.map((row) => [row.id, row]));
  for (const row of liveRows) byVacancy.set(row.id, row);
  return [...byVacancy.values()];
}

function findingsSignature(findings) {
  return JSON.stringify(findings, (key, value) => key === "highlight" || key === "problem_highlight" ? undefined : value);
}

function reviewRows(completed, source) {
  if (source === "api") {
    const archivedIds = new Set(archiveEntries.map((entry) => entry.original.id));
    return completed.filter((row) => row.findings.length && !archivedIds.has(row.id));
  }
  return completed.filter((row) => row.findings.length &&
    reviewed.get(`${source}:${row.id}`) !== findingsSignature(row.findings));
}

function entriesForView(view) {
  return archiveEntries.filter((entry) => view === "archive" || entry.status === view);
}

function renderList() {
  const liveRows = state?.active === mode ? state.jobs[mode].results : null;
  const completed = completedRows(liveRows);
  const pending = reviewRows(completed ?? [], mode);
  const queue = mode === "api" ? pending.slice(0, state?.queue_limit) : pending;
  const fromArchive = mode === "api" && archiveView !== "review";
  const selection = mode === "api" ? archiveView : mode;
  const showViews = mode === "api" && state?.followup_enabled;
  byId("vacancy-views").hidden = !showViews;
  if (showViews) {
    viewSelect.value = archiveView;
    for (const [option, label] of viewOptions) {
      const total = option.value === "review" ? queue.length : entriesForView(option.value).length;
      const text = `${label} · ${countFormat.format(total)}`;
      if (option.textContent !== text) option.textContent = text;
    }
  }
  const topic = !fromArchive && byId(`filters-${mode}`).value;
  const topicControl = byId("topic-control");
  topicControl.toggleAttribute("data-filtered", Boolean(topic));
  topicControl.title = topic ? `Тема: ${topics[topic]}` : "Темы замечаний";
  const rows = fromArchive ? entriesForView(archiveView).reverse().map((entry) => ({
    row: { ...entry.original, title: entry.latest?.title ?? entry.original.title, url: entry.latest?.url ?? entry.original.url },
    key: entry.key, entry,
  })) : queue.filter((row) => !topic || row.findings.some((finding) => finding.topic === topic))
    .map((row) => ({ row, key: row.id }));
  const shownCount = byId("shown-count");
  const count = topic && completed
    ? `Показано ${countFormat.format(rows.length)} из ${countFormat.format(queue.length)}`
    : "";
  if (shownCount.textContent !== count) shownCount.textContent = count;
  const selected = rows.find(({ key }) => key === selectedRows.get(selection)) ?? rows[0];
  if (selected) selectedRows.set(selection, selected.key);

  const list = byId("vacancy-list");
  const fragment = document.createDocumentFragment();
  for (const itemData of rows) {
    const { row, key, entry } = itemData;
    const item = clone("vacancy-template");
    const button = item.querySelector(".vacancy-button");
    const title = vacancyTitle(row);
    button.setAttribute("aria-pressed", String(itemData === selected));
    item.querySelector(".vacancy-title").textContent = title;
    const status = item.querySelector(".vacancy-status");
    const count = entry ? row.findings.filter((finding) => !entry.resolved.includes(finding.topic)).length : row.findings.length;
    const statusLabel = entry ? archiveLabels[entry.status] : pluralCount(count, "замечание", "замечания", "замечаний");
    status.setAttribute("aria-label", statusLabel);
    status.setAttribute("role", "img");
    status.title = statusLabel;
    if (count) {
      status.classList.add("status-warning");
      status.innerHTML = `<svg viewBox="0 0 28 26" aria-hidden="true"><path d="M12.7 3.2 Q14 1 15.3 3.2 L25.4 21.6 Q26.9 24 24.3 24 H3.7 Q1.1 24 2.6 21.6 Z"/><text x="14" y="19" text-anchor="middle">${count > 9 ? "9+" : count}</text></svg>`;
    } else {
      status.classList.add("status-ok");
      status.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#check"/></svg>';
    }
    const url = vacancyUrl(row.url);
    const link = item.querySelector(".row-link");
    if (url) {
      link.hidden = false;
      link.href = url;
      link.setAttribute("aria-label", `Открыть карточку вакансии «${title}»`);
    }
    button.addEventListener("click", () => {
      selectedRows.set(selection, key);
      for (const candidate of list.querySelectorAll(".vacancy-button")) {
        candidate.setAttribute("aria-pressed", String(candidate === button));
      }
      renderDetail(row, entry);
      if (matchMedia("(max-width: 690px)").matches) {
        byId("detail-title").focus({ preventScroll: true });
        byId("detail").scrollIntoView({ block: "start" });
      }
    });
    fragment.append(item);
  }
  list.replaceChildren(fragment);
  renderDetail(selected?.row, selected?.entry);
  byId("detail").hidden = !selected;
  byId("report-empty").hidden = Boolean(selected);
  const download = byId("download-report");
  const downloadHome = selected ? byId("detail-link").parentElement : byId("report-empty");
  if (download.parentElement !== downloadHome) {
    downloadHome.insertBefore(download, selected ? byId("detail-link") : null);
  }
  if (!selected) {
    const title = byId("empty-title");
    const message = byId("empty-message");
    const loading = !state || (state.revisions[mode] && !reportErrors.has(mode));
    let hint = "";
    if (fromArchive) title.textContent = "Здесь пока нет вакансий";
    else if (topic && queue.length) {
      title.textContent = "Нет вакансий по этой теме";
      hint = "Выберите другую тему или вернитесь ко всем темам.";
    } else if (state?.active === mode) title.textContent = "Проверяем вакансии";
    else if (completed) title.textContent = "Нет вакансий для разбора";
    else if (loading) title.textContent = "Загружаем отчёт";
    else {
      title.textContent = "Отчёта пока нет";
      hint = modes[mode].empty;
    }
    message.textContent = hint;
    message.hidden = !hint;
  }
}

function renderReport() {
  const entry = reportCache.get(mode);
  if (!state?.followup_enabled) archiveView = "review";
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
  byId("download-report").disabled = !entry;
  byId("download-report").title = state?.active === mode && entry
    ? "Скачать последний завершённый отчёт" : "Скачать отчёт в JSON";
  renderList();
}

async function updateReport() {
  const current = mode;
  const revision = state?.revisions[current];
  const sequence = ++reportSequence;
  if (!revision) {
    if (reportCache.delete(current)) renderReport();
    return;
  }
  if (reportCache.get(current)?.revision === revision) return;
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
    reportErrors.delete(current);
    reportCache.set(current, { data, raw, revision });
  } catch {
    if (sequence !== reportSequence) return;
    reportErrors.set(current, "Не удалось прочитать сохранённый отчёт.");
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
    const first = !state;
    const wasActive = state?.active === mode;
    const previousCount = state?.jobs[mode]?.results?.length;
    const preferencesChanged = next.followup_enabled !== state?.followup_enabled ||
      next.followup_minutes !== state?.followup_minutes ||
      next.queue_limit !== state?.queue_limit;
    const archiveChanged = next.archive_revision !== archiveRevision;
    if (archiveChanged) {
      const archive = await apiFetch("/api/archive");
      if (!archive.ok) throw new Error("Не удалось загрузить архив заявок.");
      const data = await archive.json();
      if (sequence !== refreshSequence) return;
      archiveEntries = data.entries;
      archiveRevision = data.revision;
    }
    state = next;
    connectionError = "";
    if (first) {
      mode = next.active ?? Object.keys(modes).find((key) => next.revisions[key]) ?? "api";
    }
    if (first || archiveChanged || preferencesChanged) renderReport();
    renderNotice();
    await updateReport();
    if (wasActive !== (state?.active === mode) ||
        (state?.active === mode && previousCount !== state.jobs[mode].results.length)) {
      renderReport();
    }
  } catch (error) {
    if (sequence !== refreshSequence) return;
    connectionError = error instanceof TypeError || !error.message
      ? "Нет связи с локальной программой. Проверьте, запущен ли сервер."
      : error.message;
    renderNotice();
  }
}

function switchMode(nextMode) {
  mode = nextMode;
  renderReport();
  renderNotice();
  void updateReport();
}

async function run(nextMode, upload, autoflow = false) {
  if (starting || state?.active || state?.followup_running) return;
  actionErrors.delete(nextMode);
  starting = nextMode;
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
    actionErrors.set(nextMode, error.message || "Не удалось запустить проверку.");
  } finally {
    starting = null;
    renderNotice();
  }
}

function closeSourceMenu(returnFocus = false) {
  sourceMenu.hidden = true;
  sourceTrigger.setAttribute("aria-expanded", "false");
  if (returnFocus) sourceTrigger.focus();
}

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
    if (button.dataset.mode !== mode) switchMode(button.dataset.mode);
    closeSourceMenu(true);
  });
}
document.addEventListener("pointerdown", (event) => {
  if (!sourceControls.contains(event.target)) closeSourceMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !sourceMenu.hidden) closeSourceMenu(true);
});
for (const filter of document.querySelectorAll(".topic")) {
  filter.addEventListener("change", renderList);
}
viewSelect.addEventListener("change", () => {
  archiveView = viewSelect.value;
  renderReport();
});
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
byId("run-api").addEventListener("click", () => void run("api"));
byId("auto-check").addEventListener("click", async () => {
  const active = state?.active;
  if (!active && !state?.auto_refill) {
    void run("api", null, true);
    return;
  }
  actionErrors.delete(mode);
  changingAuto = true;
  renderNotice();
  try {
    const enabled = active === "file" ? state.jobs.file.enabled : state.auto_refill;
    const action = enabled ? "pause" : "resume";
    const response = await apiFetch(`/api/queue/${action}`, { method: "POST" });
    if (!response.ok && response.status !== 409) throw new Error();
    await refresh();
  } catch {
    actionErrors.set(mode, "Не удалось переключить автопоток.");
  } finally {
    changingAuto = false;
    renderNotice();
  }
});
byId("mark-reviewed").addEventListener("click", async () => {
  if (!detailRow || detailRow.entry || markingSent) return;
  const source = mode;
  const { id, findings, content_hash } = detailRow.row;
  if (source === "api") {
    const track = !byId("followup-choice").hidden && byId("track-vacancy").checked;
    const mark = byId("mark-reviewed");
    markingSent = true;
    mark.disabled = true;
    actionErrors.delete(source);
    try {
      const response = await apiFetch("/api/archive", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, findings, content_hash, track }),
      });
      const entry = await response.json();
      if (!response.ok) throw new Error(entry.error);
      if (!archiveEntries.some(({ key }) => key === entry.key)) archiveEntries.push(entry);
      byId("copy-status").textContent = track ? "Вакансия архивирована. Отслеживание включено." : "Вакансия архивирована.";
      await refresh();
    } catch (error) {
      actionErrors.set(source, error.message || "Не удалось отметить отправку.");
      renderNotice();
      return;
    } finally {
      markingSent = false;
      mark.disabled = false;
    }
  } else {
    reviewed.set(`${source}:${id}`, findingsSignature(findings));
    try {
      localStorage.setItem(reviewedKey, JSON.stringify([...reviewed]));
    } catch {
      // Сохраняем отметку в памяти текущей страницы.
    }
  }
  if (selectedRows.get(source === "api" ? "review" : source) === id) {
    selectedRows.delete(source === "api" ? "review" : source);
  }
  renderReport();
  renderReminder();
  if (source === mode && (mode === "file" || archiveView === "review")) {
    byId(detailRow ? "detail-title" : "empty-title").focus();
  }
});
const complaintDialog = byId("complaint-dialog");
let complaintTarget = null;
byId("complain").addEventListener("click", () => {
  if (!detailRow) return;
  complaintTarget = { source: mode, vacancy: structuredClone(detailRow.row) };
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
    if (detailRow?.row.id === complaintTarget.vacancy.id && mode === complaintTarget.source) {
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
byId("choose-file").addEventListener("click", () => byId("upload-file").click());
byId("upload-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) void run("file", file);
});
byId("download-report").addEventListener("click", () => {
  const entry = reportCache.get(mode);
  if (!entry) return;
  const url = URL.createObjectURL(new Blob([entry.raw], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = modes[mode].filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

let loadedSettings;
let settingsRequest = 0;
let savingSettings = false;
const settingsDialog = byId("settings-dialog");
const followupSetting = byId("settings-followup");
const intervalSetting = byId("settings-followup-interval");
const archiveAgentSetting = byId("settings-archive-agent");
const archiveModelSelect = byId("settings-archive-model");
const modelSelect = byId("settings-model");
let settingsModels = [];


function showFollowupSettings() {
  intervalSetting.hidden = archiveAgentSetting.hidden = !followupSetting.checked;
}

function updateArchiveModels(selected) {
  const available = [...new Set([...(settingsModels ?? []), selected])].filter(Boolean);
  archiveModelSelect.replaceChildren(new Option("Нет", ""));
  for (const name of available) {
    archiveModelSelect.add(new Option(name === modelSelect.value ? `${name} (основная)` : name, name));
  }
  archiveModelSelect.value = available.includes(selected) ? selected : "";
  const availability = settingsModels === null
    ? "Ollama недоступна. Список локальных моделей загрузится, когда Ollama снова заработает."
    : available.length
      ? ""
      : "Ollama не сообщила ни об одной установленной локальной модели.";
  byId("settings-archive-note").textContent = ["При выборе «Нет» архив использует текущую основную модель.", availability]
    .filter(Boolean).join(" ");
}

followupSetting.addEventListener("change", showFollowupSettings);
modelSelect.addEventListener("change", () => updateArchiveModels(archiveModelSelect.value));

function settingsMessage(message, error = false) {
  const note = byId("settings-message");
  note.textContent = message;
  note.dataset.error = String(error);
  note.hidden = !message;
}

byId("open-settings").addEventListener("click", async () => {
  const request = ++settingsRequest;
  loadedSettings = null;
  byId("settings-config").disabled = true;
  byId("save-settings").disabled = true;
  settingsMessage("Загружаем настройки…");
  settingsDialog.showModal();
  try {
    const response = await apiFetch("/api/settings");
    const data = await response.json();
    if (request !== settingsRequest || !settingsDialog.open) return;
    if (!response.ok) throw new Error(data.error);
    loadedSettings = {
      max_vacancies: data.max_vacancies,
      model: data.model,
      archive_model: data.archive_model,
      reminder_hours: data.reminder_hours,
      followup_enabled: data.followup_enabled,
      followup_minutes: data.followup_minutes,
    };
    byId("settings-limit").max = data.max_allowed;
    byId("settings-limit").value = data.max_vacancies;
    byId("settings-reminder").value = data.reminder_hours;
    followupSetting.checked = data.followup_enabled;
    showFollowupSettings();
    byId("settings-followup-minutes").value = data.followup_minutes;
    settingsModels = data.models;
    modelSelect.replaceChildren();
    for (const name of new Set([data.model, ...(data.models ?? [])])) {
      modelSelect.add(new Option(name, name));
    }
    modelSelect.value = data.model;
    modelSelect.disabled = !data.models?.length;
    updateArchiveModels(data.archive_model);
    byId("settings-model-note").textContent = data.models === null
      ? "Ollama недоступна. Можно сохранить остальные настройки."
      : "Применяется со следующего запуска";
    byId("settings-config").disabled = false;
    byId("save-settings").disabled = false;
    settingsMessage("");
  } catch (error) {
    if (request === settingsRequest && settingsDialog.open) {
      settingsMessage(error.message || "Не удалось загрузить настройки.", true);
    }
  }
});
byId("cancel-settings").addEventListener("click", () => settingsDialog.close());
settingsDialog.addEventListener("cancel", (event) => {
  if (savingSettings) event.preventDefault();
});
settingsDialog.addEventListener("close", () => {
  settingsRequest++;
});
byId("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!loadedSettings || savingSettings) return;
  const values = {
    max_vacancies: byId("settings-limit").valueAsNumber,
    model: byId("settings-model").value,
    archive_model: archiveModelSelect.value,
    reminder_hours: Number(byId("settings-reminder").value),
    followup_enabled: followupSetting.checked,
    followup_minutes: Number(byId("settings-followup-minutes").value),
  };
  savingSettings = true;
  byId("save-settings").disabled = true;
  byId("cancel-settings").disabled = true;
  settingsMessage("");
  try {
    if (JSON.stringify(values) !== JSON.stringify(loadedSettings)) {
      const response = await apiFetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      await refresh();
    }
    settingsDialog.close();
    renderReminder();
  } catch (error) {
    settingsMessage(error.message || "Не удалось сохранить настройки.", true);
  } finally {
    savingSettings = false;
    byId("save-settings").disabled = false;
    byId("cancel-settings").disabled = false;
  }
});

async function poll() {
  await refresh();
  setTimeout(poll, state?.active || starting ? 750 : 2200);
}
void poll();
