import re

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
WORK_TIME = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b")
WORK_CYCLE = re.compile(r"(?<![\w/])(?:5\s*[/\\xх×]\s*2|2\s*[/\\xх×]\s*2)(?![\w/])", re.IGNORECASE)
WORK_INTERVAL = rf"(?:с\s+)?{WORK_TIME.pattern}\s*(?:[–—-]|до)\s*{WORK_TIME.pattern}"
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
CYCLE_HOURS = re.compile(
    rf"(?:{WORK_CYCLE.pattern}\s*(?:\([^)]{{1,80}}\)\s*)?[,;:]?\s*{WORK_INTERVAL}"
    rf"|{WORK_INTERVAL}\s*[,]?\s*{WORK_CYCLE.pattern})",
    re.IGNORECASE,
)
SHIFT_DETAIL = re.compile(r"\b(?:смен{1,2}(?:ая|ый|ой|ую|ого|ому|ые|ых|ым)\s+(?:работ\w*|график\w*)|"
                          r"по графику сменности|(?:только\s+)?в ночь|ночн\w*\s+смен\w*)\b", re.IGNORECASE)
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
PAY_FREQUENCY = re.compile(
    r"(?:выплат\w*\s+(?:заработной платы|зарплаты)\s+)?"
    r"(?:(?:\d+|один|два|три|четыре)\s+раз(?:а)?|дважды)\s+в\s+(?:месяц|неделю)[.!]?",
    re.IGNORECASE,
)
NO_EXPERIENCE = re.compile(
    r"можно без опыта(?: работы)?|опыт(?: работы)? не (?:требуется|обязателен|важен|имеет значения)|"
    r"без предъявления требований к стажу(?: работы)?|^без опыта(?: работы)?[.!]?$",
    re.IGNORECASE,
)
EXPERIENCE_MENTION = re.compile(
    r"\b(?:опыт\w*|стаж\w*|(?:от|не менее)\s+[1-9]\d*\s+(?:лет|год\w*|месяц\w*))\b",
    re.IGNORECASE,
)
EXPERIENCE_PREFERRED = re.compile(
    r"\b(?:опыт|стаж)\b(?:(?!\b(?:образование|диплом)\b)[^.!?;\n•]){0,110}?"
    r"(?:приветству\w+|(?:будет\s+)?(?:вашим\s+)?преимуществом)",
    re.IGNORECASE,
)
EXPERIENCE_MINIMUM = re.compile(
    r"\b(?:от|не менее)\s+(?:(полугода)|([1-9]\d*|одного|один|двух|два|тр[её]х|три|шести|шесть)"
    r"(?:-ти)?\s*(лет|год(?:а|ов)?|мес(?:яц(?:а|ев)?)?\.?))\b",
    re.IGNORECASE,
)
EXPERIENCE_BEFORE_DURATION = re.compile(r"\b(?:опыт|стаж)\b[^,.!?;]{0,75}$", re.IGNORECASE)
EXPERIENCE_AFTER_DURATION = re.compile(r"\s+(?:(?:практического|профессионального)\s+)?(?:опыта|стажа)\b",
                                       re.IGNORECASE)
EXPERIENCE_REQUIRED = re.compile(
    r"\b(?:опыт|стаж)\b[^.!?;\n•]{0,65}?(?<!не\s)(?:обязателен|требуется)\b",
    re.IGNORECASE,
)
EMPLOYMENT_TEXT = re.compile(r"занятост|совместител|подработк|ставк|гпх", re.IGNORECASE)
UNSPECIFIED = {"-", "—", "не указано", "не указан", "не указана", "без уточнения", "нет информации"}


def has_substantive_value(value):
    value = value.strip().casefold()
    return bool(value) and value not in UNSPECIFIED


def schedule_placement_finding(value, *schedule_values):
    hours = CYCLE_HOURS.search(value)
    pattern = CYCLE_HOURS if hours else WORK_CYCLE
    match = hours or WORK_CYCLE.search(value)
    if not match or any(pattern.search(text) for text in schedule_values):
        return None
    details = "Рабочие дни и часы указаны" if hours else "Чередование рабочих и выходных дней указано"
    return {
        "topic": "schedule", "kind": "ambiguous",
        "problem": f"{details} в поле «Требуемая квалификация».",
        "problem_highlight": "в поле «Требуемая квалификация»",
        "evidence": [{"field": "requirement.qualification", "value": match[0], "highlight": match[0]}],
        "recommendation": ("Можно указать рабочие дни и часы в описании графика работы?" if hours else
                           "Можно указать чередование рабочих и выходных дней в описании графика работы?"),
        "recommendation_source": "rule",
    }


def experience_minimum(clause):
    for match in EXPERIENCE_MINIMUM.finditer(clause):
        if (EXPERIENCE_BEFORE_DURATION.search(clause[:match.start()]) or
                EXPERIENCE_AFTER_DURATION.match(clause[match.end():])):
            return match
    return None


def experience_months(match):
    half_year, count, unit = match.groups()
    if half_year:
        return 6
    words = {"одного": 1, "один": 1, "двух": 2, "два": 2, "трех": 3, "трёх": 3, "три": 3, "шести": 6, "шесть": 6}
    number = int(count) if count.isdecimal() else words[count.casefold()]
    return number if unit.casefold().startswith("мес") else number * 12


def experience_finding(fields):
    experience = fields.get("requirement.experience", "")
    if not experience.isdecimal():
        return None
    months = int(experience) * 12
    for key in TEXT_FIELDS:
        text = fields.get(key, "")
        for clause in re.split(r"[;\n•]|(?<=[.!?])\s+", text):
            if months and (optional := NO_EXPERIENCE.search(clause.strip())):
                if "образовани" in clause.casefold() and (
                    optional[0].casefold().startswith("без предъявления") or "при наличии" in clause.casefold()
                ):
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
                if minimum_months == months or (minimum_months < 12 and months in (0, 12)):
                    continue
                value = clause.strip()
                kind = "contradiction" if months == 0 else "ambiguous"
                problem = f"В тексте и поле «Опыт работы» указаны разные минимальные сроки: {minimum_months} и {months} мес."
                question = "Какой минимальный стаж требуется кандидату?"
            elif months == 0 and (required := EXPERIENCE_REQUIRED.search(clause)):
                value = required[0]
                kind = "contradiction"
                problem = "В тексте опыт обязателен, а в поле «Опыт работы» указан нулевой стаж."
                question = "Подойдёт кандидат без опыта или требуется стаж?"
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
                if not (NO_EXPERIENCE.search(required[0]) or EXPERIENCE_PREFERRED.search(required[0])
                        or (minimum and experience_months(minimum) < 12)):
                    return {
                        "topic": "requirements", "kind": "contradiction",
                        "problem": "Опыт перечислен в разделе требований, а в поле «Опыт работы» указан нулевой стаж.",
                        "recommendation": "Подойдёт кандидат без опыта или требуется стаж?",
                        "evidence": [{"field": key, "value": required[0].strip()},
                                     {"field": "requirement.experience", "value": experience}],
                    }
    return None


def known_findings(fields):
    findings = {}
    placement = schedule_placement_finding(
        fields.get("requirement.qualification", ""), *(fields.get(key, "") for key in SCHEDULE_FIELDS)
    )
    if placement:
        findings["schedule_placement"] = placement
    comments = " ".join(fields.get(key, "") for key in TEXT_FIELDS)
    has_work_hours = len(WORK_TIME.findall(comments)) >= 2
    shift_field = next((
        key for key in reversed(SCHEDULE_FIELDS)
        if SHIFT_DETAIL.fullmatch(fields.get(key, "").strip())
    ), None)
    flexible = any(FLEXIBLE_SCHEDULE.search(fields.get(key, "")) for key in SCHEDULE_FIELDS)
    cycle_field = next((key for key in TEXT_FIELDS if WORK_CYCLE.search(fields.get(key, ""))), None)

    if not has_work_hours and shift_field:
        findings["hours"] = {
            "topic": "schedule",
            "kind": "ambiguous",
            "evidence": [{"field": shift_field, "value": fields[shift_field]}],
            "problem": "Указана сменная работа, но не указаны часы начала и окончания смен.",
            "recommendation": "Во сколько начинаются и заканчиваются смены и как распределяются рабочие дни?",
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
        if (minimum and experience_months(minimum) < 12
                and not (NO_EXPERIENCE.search(text) or EXPERIENCE_PREFERRED.search(text))):
            return True
    if kind != "contradiction":
        return False
    if topic in {"employment", "schedule"}:
        schedule = cited.get("schedule", "").casefold()
        other_schedule = next((value for field, value in cited.items() if field != "schedule"), "")
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
                if WORK_CYCLE.search(other) or SHIFT_DETAIL.search(other) or re.search(WORK_INTERVAL, other):
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
            return len(WORK_TIME.findall(text)) >= 2
    if kind == "contradiction":
        if topic == "requirements" and EXPERIENCE_MENTION.search(finding["problem"]):
            if not all(item["field"] == "requirement.experience" or EXPERIENCE_MENTION.search(item["value"])
                       for item in evidence):
                return True
        experience = next((item for item in evidence if item["field"] == "requirement.experience"), None)
        if experience:
            other = next(item["value"] for item in evidence if item is not experience)
            return not EXPERIENCE_MENTION.search(other) or (
                experience["value"] == "0" and NO_EXPERIENCE.search(other) is not None
            )
    return False
