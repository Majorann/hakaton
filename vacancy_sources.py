import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from vacancy_fields import ReportError, vacancy_result


MAX_UPLOAD = 16 * 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_VACANCIES = 1000
NOT_FOUND = object()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


PORTAL_HTTP = build_opener(NoRedirects())
OLLAMA_HTTP = build_opener(ProxyHandler({}), NoRedirects())


def read_json(stream, limit):
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ReportError(f"Размер JSON превышает {limit // (1024 * 1024)} МБ.")
    return json.loads(data.decode("utf-8-sig"))


def request_json(url, label, timeout, payload=None, *, missing_ok=False):
    headers = {"Accept": "application/json", "User-Agent": "TransparentVacancy/1.0"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        opener = OLLAMA_HTTP if urlsplit(url).hostname == "127.0.0.1" else PORTAL_HTTP
        with opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
            return read_json(response, MAX_RESPONSE)
    except HTTPError as error:
        if missing_ok and error.code in (404, 410):
            error.close()
            return NOT_FOUND
        detail = ""
        if payload is not None:
            try:
                body = read_json(error, MAX_RESPONSE)
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    detail = f" ({body['error']})"
            except (OSError, ValueError, ReportError):
                pass
        error.close()
        raise ReportError(f"{label}: HTTP {error.code}{detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ReportError(f"{label}: запрос не выполнен ({error})") from error
    except (ValueError, RecursionError) as error:
        raise ReportError(f"{label}: ответ не является корректным JSON") from error
    except ReportError as error:
        raise ReportError(f"{label}: {error}") from error


def read_vacancies(data, maximum=MAX_VACANCIES):
    if not isinstance(data, dict):
        raise ReportError("входной JSON должен содержать объект ответа API")
    if "status" in data and str(data["status"]) != "200":
        raise ReportError(f"API вернул статус {data['status']} в теле ответа")
    results = data.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("vacancies"), list):
        raise ReportError("во входном JSON нет массива results.vacancies")
    if len(results["vacancies"]) > maximum:
        raise ReportError(f"Во входном JSON больше {maximum} вакансий.")

    vacancies = []
    for index, entry in enumerate(results["vacancies"]):
        vacancy = entry.get("vacancy") if isinstance(entry, dict) else None
        identifier = vacancy.get("id") if isinstance(vacancy, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
            raise ReportError(f"в results.vacancies[{index}] ожидается vacancy.id длиной от 1 до 256 символов")
        identifier.encode("utf-8")
        vacancies.append(vacancy)
    return vacancies


def page_url(base_url, offset, page_size):
    if not isinstance(base_url, str):
        raise ReportError("api.url должен быть HTTPS-адресом портала «Работа России»")
    parts = urlsplit(base_url)
    if (parts.scheme != "https" or parts.hostname != "opendata.trudvsem.ru"
            or parts.username is not None or parts.port not in (None, 443)):
        raise ReportError("api.url должен быть HTTPS-адресом портала «Работа России»")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(offset=offset, limit=page_size)
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def available_count(data):
    meta = data.get("meta")
    value = meta.get("total") if isinstance(meta, dict) else None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def remote_batches(settings, before_work=None, *, refill=False):
    maximum = positive_int(settings, "max_vacancies", maximum=MAX_VACANCIES)
    page_size = positive_int(settings, "page_size", maximum=100)
    if not refill:
        page_size = min(page_size, maximum)
    timeout = positive_int(settings, "timeout_seconds")
    vacancies = {}
    page = 0
    scanned = 0
    previous_ids = None
    while refill or len(vacancies) < maximum:
        if page >= (MAX_VACANCIES if refill else maximum):
            if refill:
                break
            raise ReportError("API «Работа России»: достигнут предел числа страниц за запуск.")
        if before_work:
            before_work()
        label = f"API «Работа России», страница {page + 1}"
        data = request_json(page_url(settings["url"], page, page_size), label, timeout)
        try:
            entries = read_vacancies(data, page_size)
        except ReportError as error:
            raise ReportError(f"{label}: {error}") from error

        logging.info("API «Работа России» — страница %d, получено %d", page + 1, len(entries))
        if not entries:
            break
        page_ids = {vacancy["id"] for vacancy in entries}
        if page_ids == previous_ids:
            raise ReportError(f"{label}: API повторил предыдущую страницу")
        previous_ids = page_ids
        batch = []
        for vacancy in entries:
            if vacancy["id"] not in vacancies:
                vacancies[vacancy["id"]] = vacancy
                batch.append(vacancy)
            if not refill and len(vacancies) == maximum:
                break
        if batch:
            yield batch

        scanned += len(entries)
        total = available_count(data)
        if len(entries) < page_size or (total is not None and scanned >= total):
            break
        page += 1


def load_remote(settings):
    return [vacancy for batch in remote_batches(settings) for vacancy in batch]


def load_local(path):
    with path.open("rb") as input_file:
        return load_upload(input_file)


def load_upload(stream):
    try:
        return unique_vacancies(read_json(stream, MAX_UPLOAD))
    except (ValueError, RecursionError) as error:
        raise ReportError(f"Не удалось прочитать файл JSON. {error}") from error


def unique_vacancies(data):
    vacancies = {}
    for vacancy in read_vacancies(data):
        vacancies.setdefault(vacancy["id"], vacancy)
    return list(vacancies.values())


def positive_int(section, key, maximum=None):
    value = section[key]
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        bound = f" от 1 до {maximum}" if maximum is not None else " больше нуля"
        raise ReportError(f"настройка {key} должна быть целым числом{bound}")
    return value


def portal_company_code(row):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", row["id"]):
        return None
    code = row.get("company_code")
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", code):
        return code
    url = vacancy_result({"id": row["id"], "vac_url": row.get("url")}, []).get("url")
    if url:
        match = re.fullmatch(r"/vacancy/card/([A-Za-z0-9_-]{1,128})/([A-Za-z0-9_-]{1,256})/?", urlsplit(url).path)
        if match and match[2] == row["id"]:
            return match[1]
    return None


def load_portal_vacancy(settings, company_code, vacancy_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", company_code) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,256}", vacancy_id
    ):
        raise ReportError("Некорректный идентификатор вакансии или работодателя.")
    timeout = positive_int(settings, "timeout_seconds")
    url = f"https://opendata.trudvsem.ru/api/v1/vacancies/vacancy/{company_code}/{vacancy_id}"
    data = request_json(url, "API «Работа России»", timeout, missing_ok=True)
    if data is NOT_FOUND:
        return None
    rows = read_vacancies(data, 1)
    if rows and rows[0]["id"] != vacancy_id:
        raise ReportError("Портал вернул другую вакансию.")
    if not rows and (available_count(data) or 0) > 0:
        raise ReportError("Портал вернул пустой список с ненулевым количеством вакансий.")
    return rows[0] if rows else None


def ollama_chat(config, *, archive=False):
    ollama = config["ollama"]
    archive_model = ollama.get("archive_model", "") if archive else ""
    if not isinstance(archive_model, str):
        raise ReportError("ollama.archive_model должен быть строкой")
    model = archive_model or ollama["model"]
    if not isinstance(model, str) or not model.strip() or model.endswith((":cloud", "-cloud")):
        key = "archive_model" if archive_model else "model"
        raise ReportError(f"для ollama.{key} нужно имя локальной модели")
    timeout = positive_int(ollama, "timeout_seconds")

    def chat(payload):
        return request_json("http://127.0.0.1:11434/api/chat", f"Ollama ({model})", timeout, payload)

    return model, chat
