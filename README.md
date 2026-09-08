# FSVPS Requirements Monitor

Тестовый мониторинг изменений на страницах Россельхознадзора.

## Что делает

- Проверяет заданные страны.
- Загружает страницу страны.
- Находит прямые ссылки на PDF, DOC, DOCX, XLS и другие документы.
- Ищет даты изменений в тексте страницы и рядом с документами.
- Вычисляет SHA256-хэш страницы.
- Вычисляет SHA256-хэш каждого доступного документа.
- Сравнивает текущую проверку с предыдущей.
- Создаёт JSON-файлы для отображения на сайте.

## Где настраиваются страны

Откройте:

`scraper/main.py`

В начале файла находится:

```python
TARGET_COUNTRIES = {
    "Казахстан": "https://fsvps.gov.ru/importexport/kazahstan/",
    "Китай": "https://fsvps.gov.ru/importexport/kitay/",
    "Узбекистан": "https://fsvps.gov.ru/importexport/uzbekistan/",
    "Турция": "https://fsvps.gov.ru/importexport/turtsiya/",
    "Беларусь": "https://fsvps.gov.ru/importexport/belarus/",
}
```

Чтобы добавить страну:

```python
"Название страны": "полный URL страницы страны",
```

## Первый запуск

Первый запуск создаёт исходный снимок состояния (baseline).
Изменения корректно начнут определяться со второго запуска.

## Результаты

### data/monitoring.json

Полный текущий снимок.

### data/latest-changes.json

Последняя краткая сводка.

### data/changes-history.json

История запусков.

## Локальный запуск

```bash
pip install -r scraper/requirements.txt
python scraper/main.py
```

## GitHub Actions

Workflow расположен здесь:

`.github/workflows/fsvps-monitor.yml`

Он запускается автоматически каждый день и может быть запущен вручную через:

GitHub → Actions → FSVPS Requirements Monitor → Run workflow.

## Важно

Список URL пяти тестовых стран следует проверить при первом запуске.
Если конкретная страница возвращает ошибку 404 или перенаправляется,
исправьте URL в TARGET_COUNTRIES.

Перед расширением списка стран рекомендуется сначала проверить JSON
после двух последовательных запусков.
