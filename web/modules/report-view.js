import { archiveLabels, byId, clone, countFormat, modes, pluralCount, topics, vacancyTitle, vacancyUrl } from "./display.js";
import { completedRows, dashboard, entriesForView, reviewRows } from "./report-data.js";
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
    const title = vacancyTitle(row, state);
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

export function renderReport() {
  if (!dashboard.state?.followup_enabled) dashboard.archiveView = "review";
  renderSourcePicker(dashboard.mode, dashboard.archiveView);
  renderList();
}
