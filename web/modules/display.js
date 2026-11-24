export const byId = (id) => document.getElementById(id);

export const modes = {
  api: { label: "Рабочий отчёт", filename: "report.json", empty: "Загрузите вакансии с портала «Работа России»." },
  file: { label: "Локальный JSON", filename: "verification-report.json", empty: "Выберите сохранённый ответ API в формате JSON." },
};
export const topics = {
  title: "Должность", duties: "Обязанности", requirements: "Требования", pay: "Оплата",
  schedule: "График", location: "Место работы", employment: "Занятость",
};
export const kinds = { missing: "Пропуск", ambiguous: "Неоднозначность", contradiction: "Противоречие" };
const fieldLabels = {
  "job-name": "Должность", duty: "Обязанности", description: "Описание вакансии",
  "job-description": "Описание работы", requirements: "Требования", qualification: "Квалификация",
  skills: "Навыки", salary: "Зарплата", salary_min: "Минимальная зарплата",
  salary_max: "Максимальная зарплата", currency: "Валюта", schedule: "График работы",
  scheduleTypeComment: "Описание графика", shift: "Смена", employment: "Тип занятости",
  "requirement.education": "Образование", "requirement.qualification": "Дополнительные сведения по вакансии",
  "requirement.experience": "Опыт работы", "term.text": "Условия работы",
  "addresses.address": "Адрес работы", "addresses.address.location": "Адрес работы",
};

export function fieldLabel(field) {
  return fieldLabels[field.replace(/\[\d+\]/g, "")] ?? "Поле вакансии";
}


export const countFormat = new Intl.NumberFormat("ru-RU");
const plurals = new Intl.PluralRules("ru-RU");

export const pendingStatuses = new Set(["waiting", "partial"]);
export const archiveLabels = {
  waiting: "Ожидает исправления", partial: "Частично исправлена", fixed: "Исправлена",
  archived: "В архиве без отслеживания", unavailable: "Недоступна на портале",
};
export const archiveDate = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

export function pluralCount(count, one, few, many) {
  const form = { one, few, many }[plurals.select(count)] ?? many;
  return `${countFormat.format(count)} ${form}`;
}

export function vacancyTitle(row, state) {
  const reference = state?.reference_titles ?? {};
  return row.title || (Object.hasOwn(reference, row.id) ? reference[row.id] : `Вакансия ${row.id.slice(0, 8)}`);
}

export function vacancyUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && ["trudvsem.ru", "www.trudvsem.ru"].includes(url.hostname)
      && !url.username && !url.password && !url.port ? url.href : "";
  } catch {
    return "";
  }
}

export function clone(id) {
  return byId(id).content.firstElementChild.cloneNode(true);
}
