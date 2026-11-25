import { archiveDate, archiveLabels, byId, clone, fieldLabel, kinds, pendingStatuses, topics, vacancyTitle, vacancyUrl } from "./display.js";
import { dashboard } from "./report-data.js";

let shownDetail = "";

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
    if (dashboard.state?.followup_enabled) {
      const interval = dashboard.state.followup_minutes * 60;
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

function renderEditorReview(row, entry, editorBusy) {
  const panel = byId("editor-review");
  const fields = Object.entries(row?.source_fields ?? {});
  const review = row?.editor_review;
  const flagged = review?.status === "problem";
  const confirmed = review?.status === "confirmed";
  const canReview = Boolean(row && !entry && fields.length && !row.findings.length);
  panel.hidden = !canReview && !flagged;
  if (panel.hidden) return;

  const confirm = byId("editor-confirm");
  confirm.hidden = !canReview || confirmed;
  confirm.disabled = editorBusy;
  const flag = byId("editor-flag");
  flag.hidden = !canReview || (flagged && !review.error);
  flag.textContent = flagged ? "Повторить проверку Qwen" : "Есть проблема";
  flag.disabled = editorBusy;
  const notes = [];
  if (flagged) {
    notes.push(review.comment ? `Отметка редактора: ${review.comment}` : "Редактор отметил проблему.");
    if (review.error) notes.push(`Повторная проверка не завершилась. ${review.error}`);
    else if (!row.findings.length) notes.push("Qwen не нашла замечаний. Карточка остаётся на разборе.");
  } else {
    notes.push(confirmed ? "Редактор подтвердил карточку." : "По итогам анализа замечаний нет. Проверьте исходные данные.");
  }
  byId("editor-review-status").textContent = notes.join("\n");
  const list = byId("editor-source-fields");
  list.replaceChildren();
  byId("editor-source").hidden = !fields.length;
  for (const [field, value] of fields) {
    const label = document.createElement("dt");
    label.textContent = fieldLabel(field);
    const text = document.createElement("dd");
    text.textContent = value;
    list.append(label, text);
  }
}

export function renderDetail(row, entry) {
  const { mode, state } = dashboard;
  const editorBusy = Boolean(dashboard.editorSubmitting || state?.active || state?.followup_blocks_main || state?.editor_check);
  const key = JSON.stringify([mode, row, entry, editorBusy, state?.followup_enabled, state?.followup_minutes]);
  if (key === shownDetail) return;
  const preserveHistory = entry?.key && dashboard.detailRow?.entry?.key === entry.key;
  shownDetail = key;
  dashboard.detailRow = row ? { row, entry } : null;
  byId("complaint-status").hidden = true;
  const mark = byId("mark-reviewed");
  mark.hidden = !row?.findings.length || Boolean(entry);
  byId("complain").hidden = !row?.findings.length;
  mark.disabled = dashboard.markingSent;
  mark.title = mode === "api" ? "Рекомендации отправлены" : "Разобрано";
  mark.setAttribute("aria-label", mark.title);
  const trackingOption = mode === "api" && row?.findings.length && !entry && state?.followup_enabled;
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
  heading.textContent = row ? vacancyTitle(row, state) : "Замечания";
  byId("detail-id").textContent = row?.id ?? "";
  link.hidden = !url;
  if (url) link.href = url;
  else link.removeAttribute("href");

  renderEditorReview(row, entry, editorBusy);
  const detail = byId("findings");
  detail.replaceChildren();
  if (!row || !row.findings.length) {
    if (!byId("editor-review").hidden) return;
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
