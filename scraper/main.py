import json
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urldefrag

import requests
from bs4 import BeautifulSoup


# ============================================================
# НАСТРОЙКИ
# ============================================================

BASE_URL = "https://fsvps.gov.ru"

# Тестовый список стран.
# Позже для расширения мониторинга просто добавляйте страны сюда.
TARGET_COUNTRIES = {
    "Казахстан": "https://fsvps.gov.ru/importexport/kazahstan/",
    "Китай": "https://fsvps.gov.ru/importexport/kitay/",
    "Узбекистан": "https://fsvps.gov.ru/importexport/uzbekistan/",
    "Турция": "https://fsvps.gov.ru/importexport/turtsiya/",
    "Беларусь": "https://fsvps.gov.ru/importexport/belarus/",
}

REQUEST_TIMEOUT = 45
REQUEST_DELAY_SECONDS = 1.0

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

SNAPSHOT_FILE = DATA_DIR / "monitoring.json"
CHANGES_FILE = DATA_DIR / "latest-changes.json"
HISTORY_FILE = DATA_DIR / "changes-history.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FSVPSRequirementsMonitor/1.0; "
        "+https://github.com/)"
    ),
    "Accept-Language": "ru,en;q=0.8",
}

DOCUMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".odt", ".ods", ".rtf", ".zip"
)

DATE_REGEX = re.compile(
    r"(?:(обновлено|размещено|изменено|опубликовано|актуализировано)"
    r"\s*[:\-]?\s*)?"
    r"(\d{1,2}\.\d{1,2}\.\d{4})",
    re.IGNORECASE,
)


# ============================================================
# HTTP
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url, binary=False):
    """Загружает страницу или файл."""
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content if binary else response.text


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_url(url):
    url, _ = urldefrag(url)
    return url.rstrip("/")


def iso_date(date_text):
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def extract_dates(text):
    """Извлекает даты и определяет их контекст."""
    found = []

    for match in DATE_REGEX.finditer(text):
        action = (match.group(1) or "").lower()
        raw_date = match.group(2)
        normalized = iso_date(raw_date)

        if not normalized:
            continue

        if action in ("обновлено", "изменено", "актуализировано"):
            date_type = "updated"
        elif action in ("размещено", "опубликовано"):
            date_type = "published"
        else:
            date_type = "mentioned"

        found.append({
            "raw": raw_date,
            "date": normalized,
            "type": date_type,
        })

    # Удаляем дубликаты
    unique = []
    seen = set()

    for item in found:
        key = (item["date"], item["type"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def find_nearest_context(link):
    """
    Получает текст вокруг ссылки.
    Поднимаемся по DOM до нескольких уровней, чтобы найти дату,
    расположенную рядом с документом.
    """
    node = link

    for _ in range(6):
        if node is None:
            break

        text = normalize_text(node.get_text(" ", strip=True))

        # Не берём слишком большой блок страницы
        if 20 <= len(text) <= 3000:
            return text

        node = node.parent

    return normalize_text(link.get_text(" ", strip=True))


def get_document_date(link):
    context = find_nearest_context(link)
    dates = extract_dates(context)

    if not dates:
        return None

    # Приоритет обновлению
    priority = {
        "updated": 0,
        "published": 1,
        "mentioned": 2,
    }

    dates.sort(key=lambda item: priority[item["type"]])

    return dates[0]


# ============================================================
# ИЗВЛЕЧЕНИЕ ДОКУМЕНТОВ
# ============================================================

def is_document_url(url):
    clean = url.lower().split("?")[0]
    return clean.endswith(DOCUMENT_EXTENSIONS)


def extract_documents(soup, page_url):
    """
    Находит все прямые ссылки на документы.
    """
    documents = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()

        if not href:
            continue

        absolute_url = canonical_url(urljoin(page_url, href))

        if not is_document_url(absolute_url):
            continue

        if absolute_url in seen:
            continue

        seen.add(absolute_url)

        title = normalize_text(link.get_text(" ", strip=True))
        if not title:
            title = absolute_url.split("/")[-1]

        doc_date = get_document_date(link)

        documents.append({
            "title": title,
            "url": absolute_url,
            "date": doc_date["date"] if doc_date else None,
            "date_type": doc_date["type"] if doc_date else None,
            "file_hash": None,
        })

    return documents


# ============================================================
# ХЭШИРОВАНИЕ ФАЙЛОВ
# ============================================================

def update_document_hashes(documents):
    """
    Скачивает документы и вычисляет SHA256.
    Важно: именно это позволяет заметить изменение PDF,
    даже если URL файла остался прежним.
    """
    for index, document in enumerate(documents, start=1):
        url = document["url"]

        print(f"    [{index}/{len(documents)}] файл: {document['title'][:80]}")

        try:
            content = fetch(url, binary=True)
            document["file_hash"] = sha256_bytes(content)
            document["file_size"] = len(content)

        except Exception as error:
            print(f"      Не удалось скачать файл: {error}")
            document["file_hash"] = None
            document["file_size"] = None

        time.sleep(REQUEST_DELAY_SECONDS)

    return documents


# ============================================================
# ПАРСИНГ СТРАНЫ
# ============================================================

def parse_country(name, url):
    print(f"\n🇷🇺 Проверяем: {name}")
    print(f"   {url}")

    try:
        html = fetch(url)
        soup = BeautifulSoup(html, "lxml")

        # Удаляем очевидный служебный контент
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        page_text = normalize_text(soup.get_text(" ", strip=True))
        page_dates = extract_dates(page_text)

        documents = extract_documents(soup, url)

        # Для тестовой версии считаем хэши файлов.
        # Если документов слишком много, можно позже оптимизировать.
        documents = update_document_hashes(documents)

        latest_date = None
        all_dates = []

        for item in page_dates:
            all_dates.append(item["date"])

        for doc in documents:
            if doc.get("date"):
                all_dates.append(doc["date"])

        if all_dates:
            latest_date = max(all_dates)

        # Хэш текста страницы нужен для обнаружения изменений HTML
        page_hash = sha256_text(page_text)

        return {
            "name": name,
            "url": url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latest_detected_date": latest_date,
            "page_dates": page_dates,
            "page_hash": page_hash,
            "documents_count": len(documents),
            "documents": documents,
        }

    except Exception as error:
        print(f"❌ Ошибка при обработке {name}: {error}")

        return {
            "name": name,
            "url": url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": str(error),
            "documents_count": 0,
            "documents": [],
        }


# ============================================================
# СРАВНЕНИЕ SNAPSHOT
# ============================================================

def document_map(country):
    return {
        doc["url"]: doc
        for doc in country.get("documents", [])
    }


def compare_country(old, new):
    """
    Возвращает детальный список изменений страны.
    """
    if old is None:
        return {
            "status": "baseline",
            "changes": [],
        }

    changes = []

    if old.get("error") and not new.get("error"):
        changes.append({
            "type": "country_available_again",
            "message": "Страница снова успешно обработана",
        })

    if not old.get("error") and new.get("error"):
        changes.append({
            "type": "country_parse_error",
            "message": "Не удалось обработать страницу",
        })

    if (
        old.get("page_hash")
        and new.get("page_hash")
        and old.get("page_hash") != new.get("page_hash")
    ):
        changes.append({
            "type": "page_content_changed",
            "message": "Изменилось текстовое содержимое страницы",
        })

    old_docs = document_map(old)
    new_docs = document_map(new)

    # Новые документы
    for url, document in new_docs.items():
        if url not in old_docs:
            changes.append({
                "type": "document_added",
                "document": document,
            })

    # Удалённые документы
    for url, document in old_docs.items():
        if url not in new_docs:
            changes.append({
                "type": "document_removed",
                "document": document,
            })

    # Изменившиеся файлы
    for url, new_document in new_docs.items():
        old_document = old_docs.get(url)

        if not old_document:
            continue

        old_hash = old_document.get("file_hash")
        new_hash = new_document.get("file_hash")

        if old_hash and new_hash and old_hash != new_hash:
            changes.append({
                "type": "document_file_changed",
                "document": new_document,
                "message": "Содержимое документа изменилось при том же URL",
            })

        # Изменилась найденная рядом дата
        if old_document.get("date") != new_document.get("date"):
            changes.append({
                "type": "document_date_changed",
                "document": new_document,
                "old_date": old_document.get("date"),
                "new_date": new_document.get("date"),
            })

    if old.get("latest_detected_date") != new.get("latest_detected_date"):
        changes.append({
            "type": "latest_date_changed",
            "old_date": old.get("latest_detected_date"),
            "new_date": new.get("latest_detected_date"),
        })

    return {
        "status": "changed" if changes else "unchanged",
        "changes": changes,
    }


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("FSVPS IMPORT/EXPORT MONITOR")
    print("=" * 70)

    old_snapshot = load_json(SNAPSHOT_FILE, {
        "countries": {}
    })

    old_countries = old_snapshot.get("countries", {})

    new_countries = {}
    changed_countries = []
    successful = 0
    failed = 0

    for name, url in TARGET_COUNTRIES.items():
        country = parse_country(name, url)
        new_countries[name] = country

        if country.get("error"):
            failed += 1
        else:
            successful += 1

        comparison = compare_country(
            old_countries.get(name),
            country,
        )

        if comparison["status"] == "changed":
            changed_countries.append({
                "country": name,
                "url": url,
                "latest_detected_date": country.get(
                    "latest_detected_date"
                ),
                "changes": comparison["changes"],
            })

        time.sleep(REQUEST_DELAY_SECONDS)

    now = datetime.now(timezone.utc).isoformat()

    snapshot = {
        "generated_at": now,
        "countries": new_countries,
    }

    save_json(SNAPSHOT_FILE, snapshot)

    summary = {
        "generated_at": now,
        "monitor_version": "1.0",
        "countries_configured": len(TARGET_COUNTRIES),
        "countries_successfully_checked": successful,
        "countries_with_errors": failed,
        "changes_count": len(changed_countries),
        "changes": changed_countries,
    }

    save_json(CHANGES_FILE, summary)

    # История изменений
    history = load_json(HISTORY_FILE, [])
    history.append(summary)

    # Храним последние 100 запусков
    history = history[-100:]

    save_json(HISTORY_FILE, history)

    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТ")
    print("=" * 70)
    print(f"Стран в конфигурации: {len(TARGET_COUNTRIES)}")
    print(f"Успешно проверено: {successful}")
    print(f"Ошибок: {failed}")
    print(f"Стран с изменениями: {len(changed_countries)}")
    print(f"\nJSON snapshot: {SNAPSHOT_FILE}")
    print(f"JSON summary:  {CHANGES_FILE}")
    print("=" * 70)

    # Первая проверка создаёт baseline и не считается изменением
    return 0


if __name__ == "__main__":
    sys.exit(main())
