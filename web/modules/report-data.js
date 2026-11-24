export const dashboard = {
  reportCache: new Map(),
  selectedRows: new Map(),
  reportErrors: new Map(),
  actionErrors: new Map(),
  mode: "api",
  state: null,
  starting: null,
  connectionError: "",
  changingAuto: false,
  detailRow: null,
  archiveView: "review",
  archiveRevision: null,
  archiveEntries: [],
  markingSent: false,
  editorSubmitting: false,
};

const reviewedKey = "vacancy-reviewed-v1";
let reviewed = new Map();
try {
  reviewed = new Map(JSON.parse(localStorage.getItem(reviewedKey) || "[]"));
} catch {
  // Очередь на этой странице работает и без доступа к хранилищу.
}

export function completedRows(liveRows) {
  const saved = dashboard.reportCache.get(dashboard.mode)?.data.vacancies;
  if (!liveRows) return saved;
  if (dashboard.mode !== "api" || !saved) return liveRows;
  const byVacancy = new Map(saved.map((row) => [row.id, row]));
  for (const row of liveRows) byVacancy.set(row.id, row);
  return [...byVacancy.values()];
}

function findingsSignature(findings) {
  return JSON.stringify(findings, (key, value) => key === "highlight" || key === "problem_highlight" ? undefined : value);
}

export function hasReviewIssues(row) {
  return Boolean(row.findings.length) || row.editor_review?.status === "problem";
}

export function selectionKey(mode, view) {
  return `${mode}:${view}`;
}

export function cleanRows(saved, source) {
  const archivedIds = new Set(source === "api" ? dashboard.archiveEntries.map((entry) => entry.original.id) : []);
  return saved.filter((row) => row.source_fields && Object.keys(row.source_fields).length &&
    !hasReviewIssues(row) && !archivedIds.has(row.id));
}

export function reviewRows(completed, source) {
  if (source === "api") {
    const archivedIds = new Set(dashboard.archiveEntries.map((entry) => entry.original.id));
    return completed.filter((row) => hasReviewIssues(row) && !archivedIds.has(row.id));
  }
  return completed.filter((row) => hasReviewIssues(row) &&
    reviewed.get(`${source}:${row.id}`) !== findingsSignature(row.findings));
}

export function entriesForView(view) {
  return dashboard.archiveEntries.filter((entry) => view === "archive" || entry.status === view);
}

export function markReviewed(source, id, findings) {
  reviewed.set(`${source}:${id}`, findingsSignature(findings));
  try {
    localStorage.setItem(reviewedKey, JSON.stringify([...reviewed]));
  } catch {
    // Сохраняем отметку в памяти текущей страницы.
  }
}
