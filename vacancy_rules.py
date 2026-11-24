import re
from decimal import Decimal

SCHEDULE_FIELDS = ("schedule", "scheduleTypeComment")
TOPIC_FIELDS = {
    "title": ("job-name",),
    "duties": ("duty",),
    "requirements": ("requirements", "qualification", "requirement.education",
                     "requirement.qualification", "requirement.experience"),
    "pay": ("salary", "salary_min", "salary_max"),
    "schedule": SCHEDULE_FIELDS,
    "location": (),
    "employment": ("employment",),
}
TEXT_FIELDS = (
    "duty", "description", "job-description", "requirements", "qualification",
    "requirement.qualification", "term.text", "schedule", "scheduleTypeComment",
)
FLEXIBLE_SCHEDULE = re.compile(r"\bгибк\w*\b", re.IGNORECASE)
HOUR = r"(?:[01]?\d|2[0-3])"
CLOCK_TIME = re.compile(rf"\b(?:{HOUR}[:.][0-5]\d|24[:.]00)\b")
WORK_TIME = re.compile(
    rf"(?:{CLOCK_TIME.pattern}|\b{HOUR}\s*(?:час(?:ов|а)?|ч\b\.?)"
    r"(?:\s*[0-5]?\d\s*(?:минут(?:а|ы)?|мин\b\.?))?(?!\w))",
    re.IGNORECASE,
)
WORK_CYCLE = re.compile(
    r"(?<![\w/])(?:(?:5|2)\s*[/\\xх×]\s*2|"
    r"\b(?:два|пять|2|5)(?:\s+(?:рабочих\s+)?(?:дня|дней))?\s+через\s+"
    r"(?:два|2)(?:\s+(?:выходных\s+)?дня)?)(?![\w/])",
    re.IGNORECASE,
)
DASH_CLOCK = r"\b(?:[01]?\d|2[0-3])-[0-5]\d\b"
DASH_TIME = rf"(?:{CLOCK_TIME.pattern}|{DASH_CLOCK})"
WORK_INTERVAL = (
    rf"(?:(?:с\s+)?{WORK_TIME.pattern}\s*(?:[–—-]|до|по)\s*{WORK_TIME.pattern}|"
    rf"\bс\s+(?:{DASH_TIME}|{HOUR})\s+(?:до|по)\s+(?:{DASH_TIME}|{HOUR})"
    r"(?!\w|[.:-]\d|\s+(?:числа|дней|рабочих\s+дней)\b))"
)
WORK_INTERVAL_PATTERN = re.compile(WORK_INTERVAL, re.IGNORECASE)
WORK_DAYS = re.compile(r"\b(?:будн\w*|выходн\w*|понедельник\w*|вторник\w*|сред[ауе]|"
                       r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", re.IGNORECASE)
FLEXIBLE_LABELS = {"гибкий график", "режим гибкого рабочего времени"}
HOURS_CONTEXT = re.compile(r"\b(?:график\w*|рабоч\w*\s+(?:день|дня|час\w*)|время\s+работы)\b", re.IGNORECASE)
HOURS_ARRANGEMENT = re.compile(
    r"(?<!не\s)\b(?:по\s+(?:выбору|согласованию)|согласу(?:ет|ется|ются)|выбира(?:ет|ется|ются))\b",
    re.IGNORECASE,
)
PRESENCE_DETAILS = re.compile(
    rf"\b(?:(?:обязательн\w*|фиксированн\w*)\s+(?:(?:час\w*|время)\s+)?присутстви\w*)"
    rf"\s*[:—-]?\s*(?:{WORK_INTERVAL}|нет\b|не\s+(?:предусмотр\w*|требу\w*))",
    re.IGNORECASE,
)
SHIFT_DETAIL = re.compile(r"\b(?:смен{1,2}(?:ая|ый|ой|ую|ого|ому|ые|ых|ым)\s+(?:работ\w*|график\w*)|"
                          r"по графику сменности|(?:только\s+)?в ночь|ночн\w*\s+смен\w*)\b", re.IGNORECASE)
SHIFT_NAME = r"(?:дневн|ночн|утренн|вечерн)"
SHIFT_NAME_LIST = rf"{SHIFT_NAME}\w+(?:\s*(?:,|/|\b(?:и|или)\b)\s*{SHIFT_NAME}\w+)*"
SHIFT_TYPE = re.compile(
    rf"\b(?:чередование\s+{SHIFT_NAME_LIST}\s+смен[аы]?|"
    rf"смен[аы]\s*[:—–-]\s*{SHIFT_NAME_LIST}|"
    rf"(?:только\s+)?{SHIFT_NAME}(?:ая|яя|ые|ие)(?:\s*(?:,|/|\bи\b)\s*{SHIFT_NAME}\w+)*\s+смен[аы]|"
    rf"работа\s+(?:только\s+)?в\s+(?:ночь|{SHIFT_NAME}(?:ую|юю)\s+смену)|"
    r"только\s+в\s+ночь|"
    r"день\s*/\s*ночь|ночь\s*/\s*день)\b",
    re.IGNORECASE,
)
ADDRESS_SHIFT = re.compile(rf"\b{SHIFT_NAME}(?:ая|яя)\s+смена[.\s]*$", re.IGNORECASE)
SHIFT_ORDINAL = r"(?:первая|вторая|третья|четв[её]ртая)"
SHIFT_SEQUENCE = re.compile(
    rf"\b{SHIFT_ORDINAL}(?:\s*(?:,|\bи\b)\s*{SHIFT_ORDINAL})+\s+смены\b", re.IGNORECASE,
)
NUMBERED_SHIFT_HOURS = re.compile(
    rf"\b({SHIFT_ORDINAL})\s+смена\s*[:—–-]?\s*{WORK_INTERVAL}", re.IGNORECASE,
)
NIGHT_SHIFT_START = re.compile(
    rf"\bс\s+{DASH_TIME}\s+только\s+в\s+ночь\b", re.IGNORECASE,
)
SHIFT_TYPE_QUESTION = re.compile(
    rf"\b(?:каки[ея]\s+(?:(?:предусмотрен[аы]|доступн[аы])\s+)?смен[аы]|"
    rf"какие\s+(?:типы|виды)\s+смен|какой\s+(?:тип|вид)\s+смены|"
    rf"(?:есть|предусмотрен[аы])\s+ли\s+{SHIFT_NAME_LIST}\s+смен[аы])\b",
    re.IGNORECASE,
)
SHIFT_DETAILS_QUESTION = re.compile(
    r"\b(?:час\w*|врем\w*|начина\w*|заканч\w*|череду\w*|распредел\w*|длительность|сколько)\b",
    re.IGNORECASE,
)
SHIFT_FIELD_ADVICE = re.compile(r"\bпол[ея]\s+[«\"]?смена\b", re.IGNORECASE)
EXPLICIT_WORK_CONFLICT = re.compile(
    r"\b(?:не\s*полн\w*\s+(?:рабоч\w*\s+д\w*|занятост\w*)|частичн\w*\s+занятост\w*|"
    r"полставки|0[.,]\d+\s*ставк\w*|без\s+смен|смен\w*\s+нет|сменн\w*\s+работ\w*\s+нет)\b",
    re.IGNORECASE,
)
PART_TIME_OPTION = re.compile(
    r"(?<!не\s)\b(?:возмож(?:ен|на|ны)|допускается)\s+неполн\w*\s+(?:рабоч\w*|занятост\w*)",
    re.IGNORECASE,
)
PARTIAL_WORKLOAD = re.compile(r"\b0[.,]\d+\s*(?:ставк\w*|штатн\w*\s+единиц\w*)\b", re.IGNORECASE)
WORKLOAD_QUESTION = re.compile(r"\b(?:дол\w*|размер\w*|сколько)\s+(?:штатной\s+)?ставк\w*\b", re.IGNORECASE)
PROFESSIONAL_EDUCATION_LEVEL = re.compile(
    r"(?:высшее(?:\s+или\s+среднее)?|среднее\s+профессиональное)(?:\s+\w+)?\s+образование",
    re.IGNORECASE,
)
PAY_FREQUENCY = re.compile(
    r"(?:выплат\w*\s+(?:заработной платы|зарплаты)\s+)?"
    r"(?:(?:\d+|один|два|три|четыре)\s+раз(?:а)?|дважды)\s+в\s+(?:месяц|неделю)[.!]?",
    re.IGNORECASE,
)
NO_EXPERIENCE = re.compile(
    r"можно без опыта(?: работы)?|опыт(?: работы)? не (?:требуется|обязателен|важен|имеет значения)|"
    r"без предъявления требований к стажу(?: работы)?|^без опыта(?: работы)?[.!]?$|"
    r"\bтребования\s+к\s+(?:стажу|опыту)\b[^.!?;\n]{0,120}?\s+не\s+предъявляются\b",
    re.IGNORECASE,
)
EXPERIENCE_MENTION = re.compile(
    r"\b(?:опыт\w*|стаж\w*|(?:от|не менее)\s+[1-9]\d*\s+(?:лет|год\w*|месяц\w*))\b",
    re.IGNORECASE,
)
EXPERIENCE_PREFERRED = re.compile(
    r"\b(?:опыт|стаж)\b(?:(?!\b(?:образование|диплом)\b)[^.!?;\n•]){0,110}?"
    r"(?:приветству\w+|желателен\b|(?:будет\s+)?(?:вашим\s+)?преимуществом)",
    re.IGNORECASE,
)
NUMBER_WORDS = {
    "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6,
    "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
}
DURATION_COUNT = rf"(?:(?:[1-9]\d*(?:[.,]\d+)?|0[.,]\d*[1-9]\d*)(?:-(?:ти|х|го))?|{'|'.join(NUMBER_WORDS)}|полтора|полутора)"
YEARS = r"(?:лет|год(?:а|ов)?|г)"
MONTHS = r"мес(?:яц(?:а|ев)?)?\b\.?"
SHORT_DURATION = r"(?:день|дня|дней|дн\b\.?|час(?:а|ов)?|ч\b\.?)"
EXPERIENCE_MINIMUM = re.compile(
    rf"\b(?:от|не менее)\s+(?:(?P<half>полу?года)\b|"
    rf"(?:(?P<years>{DURATION_COUNT})\s*{YEARS}|(?P<one_year>года?))\b\.?"
    rf"(?:\s+(?:и\s+)?(?P<extra_months>{DURATION_COUNT})\s*{MONTHS})?|"
    rf"(?P<months>{DURATION_COUNT})\s*{MONTHS}|"
    rf"(?P<short>{DURATION_COUNT})\s*(?:рабочих\s+|календарных\s+)?{SHORT_DURATION}\b\.?)",
    re.IGNORECASE,
)
EXPERIENCE_CLAUSE_BREAK = re.compile(
    r"[;\n•]|(?<!\bг\.)(?<!\bмес\.)(?<!\bдн\.)(?<!\bч\.)(?<=[.!?])\s+", re.IGNORECASE,
)
EXPERIENCE_BEFORE_DURATION = re.compile(r"\b(?:опыт|стаж)\b[^,.!?;]{0,75}$", re.IGNORECASE)
EXPERIENCE_AFTER_DURATION = re.compile(r"\s+(?:(?:практического|профессионального)\s+)?(?:опыта|стажа)\b",
                                       re.IGNORECASE)
EXPERIENCE_REQUIRED = re.compile(
    r"\b(?:опыт|стаж)\b[^.!?;\n•]{0,65}?(?<!не\s)(?:обязателен|требуется)\b",
    re.IGNORECASE,
)
EMPLOYMENT_TEXT = re.compile(r"\b(?:занятост\w*|совместител\w*|подработк\w*|(?:пол)?ставк\w*|гпх)\b", re.IGNORECASE)
EXPERIENCE_PRESENCE = re.compile(r"\bналичие\s+(?:профессионального\s+)?опыта\b", re.IGNORECASE)
TEACHING_TITLE = re.compile(r"\bучитель\s+(английского\s+языка|химии)\b", re.IGNORECASE)
TEACHING_SUBJECT = re.compile(r"\bпреподаваемого предмета\s*[:—«]?\s*(химия|английский\s+язык)\b", re.IGNORECASE)
UNSPECIFIED = {"-", "—", "не указано", "не указан", "не указана", "без уточнения", "нет информации"}


def contains_work_hours(text):
    return len(CLOCK_TIME.findall(text)) >= 2 or WORK_INTERVAL_PATTERN.search(text) is not None


def has_substantive_value(value):
    value = value.strip().casefold()
    return bool(value) and value not in UNSPECIFIED


def structured_shift(fields):
    return next((
        {"field": key, "value": value} for key, value in fields.items()
        if (key == "shift" or key.startswith("shift[")) and has_substantive_value(value)
    ), None)


def described_shift(fields):
    for key, text in fields.items():
        if key.partition("[")[0] not in TEXT_FIELDS:
            continue
        for match in SHIFT_TYPE.finditer(text):
            if re.search(r"\b(?:без|нет|не|не предусмотрены)\s+$", text[:match.start()], re.IGNORECASE):
                continue
            if re.match(r"\s+(?:отсутств\w*|не\s+(?:предусмотр\w*|требу\w*))\b", text[match.end():], re.IGNORECASE):
                continue
            return {"field": key, "value": match[0]}
    return None


def incomplete_shift_hours(text_fields, has_work_hours):
    covered = {
        match[1].casefold() for text in text_fields.values() for match in NUMBERED_SHIFT_HOURS.finditer(text)
    }
    for key, text in text_fields.items():
        if sequence := SHIFT_SEQUENCE.search(text):
            shifts = re.findall(SHIFT_ORDINAL, sequence[0].casefold())
            missing = [shift for shift in shifts if shift not in covered]
            if missing:
                question = (
                    f"Во сколько начинается и заканчивается {missing[0]} смена?" if len(missing) == 1
                    else f"Укажите часы работы для каждой смены ({', '.join(missing)})."
                )
                return {
                    "topic": "schedule", "kind": "ambiguous",
                    "evidence": [{"field": key, "value": sequence[0]}],
                    "problem": "В расписании отсутствуют часы начала и окончания для " + (
                        "части смен." if len(missing) < len(shifts) else "перечисленных смен."
                    ),
                    "recommendation": question,
                }
        if not has_work_hours and (start := NIGHT_SHIFT_START.search(text)):
            return {
                "topic": "schedule", "kind": "ambiguous",
                "evidence": [{"field": key, "value": start[0]}],
                "problem": "Указано время начала ночной смены, время окончания отсутствует.",
                "recommendation": "Во сколько заканчивается ночная смена?",
            }
    return None


def experience_minimum(clause):
    for match in EXPERIENCE_MINIMUM.finditer(clause):
        if (EXPERIENCE_BEFORE_DURATION.search(clause[:match.start()]) or
                EXPERIENCE_AFTER_DURATION.match(clause[match.end():])):
            return match
    return None


def duration_count(value):
    value = value.casefold()
    if value in NUMBER_WORDS:
        return NUMBER_WORDS[value]
    if value in {"полтора", "полутора"}:
        return Decimal("1.5")
    return Decimal(value.split("-")[0].replace(",", "."))


def experience_months(match):
    if match["half"]:
        return 6
    if match["months"]:
        return duration_count(match["months"])
    if match["short"]:
        return None
    months = 12 * duration_count(match["years"] or "1")
    return months + duration_count(match["extra_months"] or "0")


def experience_finding(fields):
    experience = fields.get("requirement.experience", "")
    if not experience.isdecimal():
        return None
    months = int(experience) * 12
    for key in TEXT_FIELDS:
        text = fields.get(key, "")
        for clause in EXPERIENCE_CLAUSE_BREAK.split(text):
            if months and (optional := NO_EXPERIENCE.search(clause.strip())):
                if "при наличии" in clause.casefold() and "образовани" in clause.casefold():
                    value = clause.strip()
                    kind = "ambiguous"
                    problem = "Требование к стажу в тексте связано с образованием, а в поле «Опыт работы» указан общий минимум."
                    question = "Для какого образования требуется стаж и какой минимум применяется?"
                else:
                    value = optional[0]
                    kind = "contradiction"
                    problem = "Текст допускает работу без опыта, а в структурированном поле указан опыт больше нуля."
                    question = "Может ли кандидат устроиться без опыта или требуется минимальный стаж?"
            elif months and (preferred := EXPERIENCE_PREFERRED.search(clause)):
                value = preferred[0]
                kind = "ambiguous"
                problem = "В тексте опыт указан как преимущество, а в поле «Опыт работы» задан минимальный стаж."
                question = "Этот стаж обязателен или опыт учитывается как преимущество?"
            elif minimum := experience_minimum(clause):
                minimum_months = experience_months(minimum)
                if minimum_months is None or minimum_months == months or (minimum_months < 12 and months in (0, 12)):
                    continue
                value = clause.strip()
                kind = "ambiguous"
                problem = (f"В тексте требуется стаж от {format(minimum_months, 'f').removesuffix('.0')} мес., "
                           f"в поле «Опыт работы» указано {months} мес.")
                question = "Какой минимальный стаж требуется кандидату?"
            elif months == 0 and not EXPERIENCE_PREFERRED.search(clause) and (
                required := EXPERIENCE_REQUIRED.search(clause)
                or (EXPERIENCE_PRESENCE.search(clause) if key == "requirements" else None)
            ):
                value = required[0]
                kind = "ambiguous"
                problem = "В тексте указан опыт, а в поле «Опыт работы» стоит 0."
                question = "Нужен ли опыт и, если нужен, какой минимальный стаж требуется?"
            else:
                continue
            return {
                "topic": "requirements", "kind": kind, "problem": problem, "recommendation": question,
                "evidence": [{"field": key, "value": value}, {"field": "requirement.experience", "value": experience}],
            }
        if months == 0 and key == "duty":
            reqs = re.search(r"\bтребования\s*[:•](.*?)(?:базовый социальный пакет|условия\s*[:•]|$)",
                             text, re.IGNORECASE | re.DOTALL)
            if reqs and (required := re.search(r"\bопыт работы\s+[^.;•\n]+", reqs[1], re.IGNORECASE)):
                minimum = experience_minimum(required[0])
                minimum_months = experience_months(minimum) if minimum else None
                if not (NO_EXPERIENCE.search(required[0]) or EXPERIENCE_PREFERRED.search(required[0])
                        or (minimum and (minimum_months is None or minimum_months < 12))):
                    return {
                        "topic": "requirements", "kind": "ambiguous",
                        "problem": "Опыт перечислен в разделе требований, а в поле «Опыт работы» стоит 0.",
                        "recommendation": "Подойдёт кандидат без опыта или требуется стаж?",
                        "evidence": [{"field": key, "value": required[0].strip()},
                                     {"field": "requirement.experience", "value": experience}],
                    }
    return None


def teaching_subject_finding(fields):
    title = TEACHING_TITLE.search(fields.get("job-name", ""))
    subject = TEACHING_SUBJECT.search(fields.get("duty", ""))
    if not (title and subject):
        return None
    if title[1].casefold().startswith("английск") == subject[1].casefold().startswith("английск"):
        return None
    return {
        "topic": "duties", "kind": "contradiction",
        "evidence": [{"field": "job-name", "value": title[0]}, {"field": "duty", "value": subject[0]}],
        "problem": "В названии вакансии и в обязанностях указаны разные предметы преподавания.",
        "recommendation": "Какой предмет должен преподавать сотрудник?",
    }


def known_findings(fields):
    findings = {}
    if teaching := teaching_subject_finding(fields):
        findings["teaching_subject"] = teaching
    text_fields = {key: value for key, value in fields.items() if key.partition("[")[0] in TEXT_FIELDS}
    comments = " ".join(text_fields.values())
    has_work_hours = contains_work_hours(comments)
    shift = structured_shift(fields)
    shift_in_text = described_shift(fields)
    if shift_in_text and not shift:
        findings["shift_placement"] = {
            "topic": "schedule",
            "kind": "ambiguous",
            "evidence": [shift_in_text],
            "problem": "Режим смен указан в тексте вакансии, поле «Смена» не заполнено.",
            "recommendation": "Заполните поле «Смена» в соответствии с режимом, указанным в тексте вакансии.",
        }
    elif not shift:
        for key, value in fields.items():
            if key.startswith("addresses.address[") and (match := ADDRESS_SHIFT.search(value)):
                findings["shift_placement"] = {
                    "topic": "schedule",
                    "kind": "ambiguous",
                    "evidence": [{"field": key, "value": match[0].strip()}],
                    "problem": "В адресе упоминается смена, а поле «Смена» пустое.",
                    "recommendation": "Относится ли указанная в адресе смена к этой вакансии? Если да, заполните поле «Смена».",
                }
                break
    shift_field = next((
        key for key in reversed(SCHEDULE_FIELDS)
        if SHIFT_DETAIL.fullmatch(fields.get(key, "").strip())
    ), None)
    flexible = any(FLEXIBLE_SCHEDULE.search(fields.get(key, "")) for key in SCHEDULE_FIELDS)
    cycle_field = next((key for key in TEXT_FIELDS if WORK_CYCLE.search(fields.get(key, ""))), None)

    shift_evidence = shift or shift_in_text
    if not shift_evidence and shift_field:
        shift_evidence = {"field": shift_field, "value": fields[shift_field]}
    if hours := incomplete_shift_hours(text_fields, has_work_hours):
        findings["hours"] = hours
    elif not has_work_hours and shift_evidence:
        findings["hours"] = {
            "topic": "schedule",
            "kind": "ambiguous",
            "evidence": [shift_evidence],
            "problem": "Для указанных смен не описаны часы начала и окончания.",
            "recommendation": "Во сколько начинаются и заканчиваются смены?",
        }
    elif not has_work_hours and cycle_field and not flexible:
        findings["hours"] = {
            "topic": "schedule",
            "kind": "ambiguous",
            "evidence": [{"field": cycle_field, "value": WORK_CYCLE.search(fields[cycle_field])[0]}],
            "problem": "Указано чередование рабочих и выходных дней, но не указаны часы работы.",
            "recommendation": "Во сколько начинается и заканчивается рабочий день?",
        }

    if not has_substantive_value(fields.get("employment", "")) and not EMPLOYMENT_TEXT.search(comments):
        findings["employment"] = {
            "topic": "employment",
            "kind": "missing",
            "evidence": [{"field": "employment", "value": ""}],
            "problem": "Тип занятости не указан.",
            "recommendation": "Какой тип занятости предлагается по этой вакансии?",
        }

    if experience := experience_finding(fields):
        findings["experience"] = experience
    return findings


def compatible_citations(finding):
    kind = finding.get("kind")
    topic = finding.get("topic")
    evidence = finding.get("evidence", [])
    if kind == "ambiguous" and topic in {"employment", "schedule"} and len(evidence) == 1:
        quote = evidence[0]
        value = quote["value"].strip().casefold()
        if (topic == "schedule" and finding.get("recommendation_source") == "rule"
                and quote["field"] == "requirement.qualification"
                and finding.get("problem") in {
                    "Рабочие дни и часы указаны в поле «Требуемая квалификация».",
                    "Чередование рабочих и выходных дней указано в поле «Требуемая квалификация».",
                }):
            return True
        if (topic == "schedule" and finding.get("recommendation_source") == "rule"
                and quote["field"] in SCHEDULE_FIELDS
                and FLEXIBLE_SCHEDULE.search(value)
                and finding.get("problem") == "Указан тип графика, но не указаны часы начала и окончания смен."):
            return True
        if quote["field"] == "employment" and value == "полная занятость":
            return True
        if topic == "schedule" and quote["field"] == "schedule" and value == "полный рабочий день":
            return "не соответствует реальности" in finding.get("problem", "").casefold()
    if len(evidence) != 2:
        return False
    cited = {item["field"]: item["value"].strip() for item in evidence}
    if topic == "requirements" and cited.get("requirement.experience") in {"0", "1"}:
        text = next((value for key, value in cited.items() if key != "requirement.experience"), "")
        minimum = experience_minimum(text) or EXPERIENCE_MINIMUM.fullmatch(text)
        minimum_months = experience_months(minimum) if minimum else None
        if (minimum and (minimum_months is None or minimum_months < 12)
                and not (NO_EXPERIENCE.search(text) or EXPERIENCE_PREFERRED.search(text))):
            return True
    if kind != "contradiction":
        return False
    if topic == "schedule":
        if set(cited) == {"job-name", "duty"} and teaching_subject_finding(cited):
            return True
        if any(WORK_CYCLE.fullmatch(value) for value in cited.values()) and any(
            re.match(r"неполный рабочий день", value, re.IGNORECASE) or PART_TIME_OPTION.search(value)
            for value in cited.values()
        ):
            return True
    if topic in {"employment", "schedule"}:
        schedule = cited.get("schedule", "").casefold()
        other_schedule = next((value for field, value in cited.items() if field != "schedule"), "")
        if topic == "schedule" and schedule and other_schedule:
            if not any(pattern.search(other_schedule) for pattern in (
                HOURS_CONTEXT, WORK_TIME, WORK_CYCLE, SHIFT_DETAIL, SHIFT_TYPE,
                FLEXIBLE_SCHEDULE, EXPLICIT_WORK_CONFLICT, WORK_DAYS, WORK_INTERVAL_PATTERN,
            )):
                return True
        if schedule.startswith("неполный рабочий день") and (
            PART_TIME_OPTION.search(other_schedule) or (
                PARTIAL_WORKLOAD.search(other_schedule) and
                not re.search(r"\b(?:полная занятость|полный рабочий день)\b", other_schedule, re.IGNORECASE)
            )
        ):
            return True
        if topic == "employment" and schedule.startswith("неполный рабочий день") and "duty" in cited:
            duty = cited["duty"]
            if not (EMPLOYMENT_TEXT.search(duty) or WORK_TIME.search(duty) or WORK_CYCLE.search(duty)
                    or SHIFT_DETAIL.search(duty) or re.search(r"\bполн\w*\s+рабоч\w*\s+д\w*\b", duty, re.IGNORECASE)):
                return True
        if schedule == "полный рабочий день" and PART_TIME_OPTION.search(other_schedule):
            return True
        for field, generic in (("employment", "полная занятость"), ("schedule", "полный рабочий день"),
                               ("schedule", "сменная работа")):
            if cited.get(field, "").casefold() == generic:
                other = next((value for key, value in cited.items() if key != field), "")
                if EXPLICIT_WORK_CONFLICT.search(other):
                    continue
                if WORK_CYCLE.search(other) or SHIFT_DETAIL.search(other) or WORK_INTERVAL_PATTERN.search(other):
                    return True
    if topic == "pay" and any(cited.get(key, "").isdecimal() for key in ("salary_min", "salary_max")):
        return any(PAY_FREQUENCY.fullmatch(value) for value in cited.values())
    return False


def flexible_hours_explained(text):
    return PRESENCE_DETAILS.search(text) and any(
        HOURS_CONTEXT.search(clause) and HOURS_ARRANGEMENT.search(clause)
        for clause in re.split(r"[!?;\n]|(?<=\.)\s+", text)
    )


def compatible_facts(finding, fields):
    topic, kind = finding["topic"], finding["kind"]
    evidence = finding["evidence"]
    if compatible_citations(finding):
        return True
    if topic == "employment" and kind in {"missing", "ambiguous"}:
        if WORKLOAD_QUESTION.search(finding["recommendation"]) and any(
            PARTIAL_WORKLOAD.search(value) for key, value in fields.items()
            if key == "job-name" or key.partition("[")[0] in TEXT_FIELDS
        ):
            return True
    if kind == "missing":
        if any(
            has_substantive_value(fields.get(key, "")) and (topic != "pay" or fields[key] != "0")
            for key in TOPIC_FIELDS[topic]
        ):
            return True
        if topic == "location":
            return any(key.startswith("addresses.address[") and has_substantive_value(value)
                       for key, value in fields.items())
    if topic == "schedule":
        if kind in {"missing", "ambiguous"}:
            shift = structured_shift(fields)
            shift_in_text = described_shift(fields)
            if kind == "missing" and (shift or shift_in_text):
                return True
            if kind == "ambiguous":
                question = finding["recommendation"]
                if shift_in_text and not shift and SHIFT_FIELD_ADVICE.search(question):
                    return True
                if (shift_in_text or (shift and SHIFT_TYPE.search(shift["value"]))) and (
                    SHIFT_TYPE_QUESTION.search(question) and not SHIFT_DETAILS_QUESTION.search(question)
                ):
                    return True
        if any(quote["field"] in SCHEDULE_FIELDS and quote["value"].strip().casefold() in FLEXIBLE_LABELS
               for quote in evidence):
            texts = (quote["value"] for quote in evidence) if kind == "contradiction" else (
                fields.get(key, "") for key in TEXT_FIELDS
            )
            if any(flexible_hours_explained(text) for text in texts):
                return True
        schedule = fields.get("schedule", "").casefold()
        if kind == "contradiction" and schedule == "сменная работа":
            cited_shifts = {x["value"].casefold() for x in evidence if x["field"].startswith("shift[")}
            if cited_shifts and cited_shifts <= {"только дневная смена", "только ночная смена"}:
                return any(x["field"] == "schedule" for x in evidence)
        if kind == "ambiguous" and schedule == "полный рабочий день":
            text = " ".join(fields.get(field, "") for field in TEXT_FIELDS)
            return contains_work_hours(text)
    if kind == "contradiction":
        if topic == "requirements":
            values = {item["value"].strip().casefold() for item in evidence}
            if "профессиональное образование" in values and any(
                PROFESSIONAL_EDUCATION_LEVEL.fullmatch(value) for value in values
            ):
                return True
        if topic == "requirements" and EXPERIENCE_MENTION.search(finding["problem"]):
            if not all(item["field"] == "requirement.experience" or EXPERIENCE_MENTION.search(item["value"])
                       for item in evidence):
                return True
        experience = next((item for item in evidence if item["field"] == "requirement.experience"), None)
        if experience:
            other = next(item["value"] for item in evidence if item is not experience)
            return experience["value"] == "0" or not EXPERIENCE_MENTION.search(other)
    return False
