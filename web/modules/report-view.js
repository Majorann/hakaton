import { archiveLabels, byId, clone, countFormat, modes, pluralCount, topics, vacancyTitle, vacancyUrl } from "./display.js";
import { cleanRows, completedRows, dashboard, entriesForView, reviewRows, selectionKey } from "./report-data.js";
import { renderDetail } from "./detail.js";
import { renderSourcePicker } from "./source-picker.js";

const viewSelect = byId("vacancy-view");
const viewOptions = [...viewSelect.options].map((option) => [option, option.textContent]);

for (const [value, label] of Object.entries(topics)) {
  for (const select of document.querySelectorAll(".topic")) select.add(new Option(label, value));
}

for (const filter of document.querySelectorAll(".topic")) {
  filter.addEventListener("change", renderList);
}
viewSelect.addEventListener("change", () => {
  dashboard.archiveView = viewSelect.value;
  renderReport();
});

function renderList() {
  const { state, mode, archiveView, selectedRows, reportErrors } = dashboard;
  const liveRows = state?.active === mode ? state.jobs[mode].results : null;
  const completed = completedRows(liveRows);
  const pending = reviewRows(completed ?? [], mode);
  const manual = pending.filter((row) => row.editor_review?.status === "problem");
  const regular = pending.filter((row) => row.editor_review?.status !== "problem");
  const queue = [...manual, ...(mode === "api" ? regular.slice(0, Math.max(0, (state?.queue_limit ?? regular.length) - manual.length)) : regular)];
  const archiveEnabled = mode === "api" && state?.followup_enabled;
  const fromArchive = archiveEnabled && !["review", "clean", "confirmed"].includes(archiveView);
  const selection = selectionKey(mode, archiveView);
  const clean = cleanRows(dashboard.reportCache.get(mode)?.data.vacancies ?? [], mode);
  const sections = {
    review: queue,
    clean: clean.filter((row) => row.editor_review?.status !== "confirmed"),
    confirmed: clean.filter((row) => row.editor_review?.status === "confirmed"),
  };
  byId("vacancy-views").hidden = !state;
  byId("archive-views").hidden = !archiveEnabled;
  viewSelect.value = archiveView;
  for (const [option, label] of viewOptions) {
    const total = (sections[option.value] ?? entriesForView(option.value)).length;
    const text = `${label} · ${countFormat.format(total)}`;
    if (option.textContent !== text) option.textContent = text;
  }
  const topic = archiveView === "review" && byId(`filters-${mode}`).value;
  const topicControl = byId("topic-control");
  topicControl.toggleAttribute("data-filtered", Boolean(topic));
  topicControl.title = topic ? `Тема: ${topics[topic]}` : "Темы замечаний";
  const rows = fromArchive ? entriesForView(archiveView).reverse().map((entry) => ({
    row: { ...entry.original, title: entry.latest?.title ?? entry.original.title, url: entry.latest?.url ?? entry.original.url },
    key: entry.key, entry,
  })) : sections[archiveView].filter((row) => !topic || row.findings.some((finding) => finding.topic === topic))
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
    const title = vacancyTitle(row, state);
    button.setAttribute("aria-pressed", String(itemData === selected));
    item.querySelector(".vacancy-title").textContent = title;
    const status = item.querySelector(".vacancy-status");
    const count = entry ? row.findings.filter((finding) => !entry.resolved.includes(finding.topic)).length : row.findings.length;
    const editorFlag = row.editor_review?.status === "problem";
    const statusLabel = entry ? archiveLabels[entry.status]
      : editorFlag && !count ? "Проблема отмечена редактором"
      : pluralCount(count, "замечание", "замечания", "замечаний");
    status.setAttribute("aria-label", statusLabel);
    status.setAttribute("role", "img");
    status.title = statusLabel;
    if (count || editorFlag) {
      status.classList.add("status-warning");
      status.innerHTML = `<svg viewBox="0 0 28 26" aria-hidden="true"><path d="M12.7 3.2 Q14 1 15.3 3.2 L25.4 21.6 Q26.9 24 24.3 24 H3.7 Q1.1 24 2.6 21.6 Z"/><text x="14" y="19" text-anchor="middle">${count > 9 ? "9+" : count || "!"}</text></svg>`;
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
    else if (completed) {
      title.textContent = {
        clean: "Нет вакансий для проверки редактором",
        confirmed: "Пока нет подтверждённых вакансий",
      }[archiveView] ?? "Нет вакансий для разбора";
    }
    else if (loading) title.textContent = "Загружаем отчёт";
    else {
      title.textContent = "Отчёта пока нет";
      hint = modes[mode].empty;
    }
    message.textContent = hint;
    message.hidden = !hint;
  }
}

export function renderReport() {
  if ((dashboard.mode !== "api" || !dashboard.state?.followup_enabled) &&
      !["review", "clean", "confirmed"].includes(dashboard.archiveView)) dashboard.archiveView = "review";
  renderSourcePicker(dashboard.mode, dashboard.archiveView);
  renderList();
}
