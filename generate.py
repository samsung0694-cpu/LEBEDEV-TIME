import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from google import genai

API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

FEEDS = [
    "https://www.interfax.ru/rss.asp",
    "https://tass.ru/rss/v2.xml",
    "https://lenta.ru/rss",
    "https://www.kommersant.ru/rss/news.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.vedomosti.ru/rss/rubric/politics",
    "https://www.vedomosti.ru/rss/rubric/economics",
    "https://www.vedomosti.ru/rss/rubric/technology",
    "https://habr.com/ru/rss/all/all/",
]
def collect_news():
    items = []

    for url in FEEDS:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "LEBEDEV-TIME/1.0"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                root = ET.fromstring(response.read())

            for item in root.findall(".//item")[:50]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                description = item.findtext("description", "")

                if title and link:
                    items.append({
                        "title": title,
                        "link": link,
                        "description": description
                    })
        except Exception as error:
            print(f"RSS error: {error}")

    return items

news = collect_news()

if not news:
    raise RuntimeError("Не удалось получить новости из RSS-лент")

prompt = f"""
Ты — главный редактор ежедневного электронного журнала LEBEDEV TIME.

Подготовь полноценный ежедневный новостной выпуск на русском языке.

ОБЯЗАТЕЛЬНЫЕ РАЗДЕЛЫ:
1. Политика
2. Закон и право
3. Общество
4. Экономика
5. Технологии
6. Психология и поведение людей
7. Необычные истории и загадки

РЕДАКЦИОННЫЕ ПРАВИЛА:

1. Проверь все предоставленные материалы.
2. Стремись включить минимум 2 статьи в каждый раздел.
3. Отбирай наиболее значимые, интересные и свежие события.
4. Не дублируй одну новость в нескольких разделах.
5. Не менее тщательно рассматривай психологию и необычные истории.
6. Для раздела «Закон и право» выбирай новости законодательства,
судебной практики, правоохранительной деятельности и прав человека.
7. Для раздела «Психология и поведение людей» выбирай материалы
о поведении, мышлении, социальных экспериментах и исследованиях.
8. Для раздела «Необычные истории и загадки» выбирай необычные,
документированные события, открытия и расследования.
9. Не выдумывай события, факты, даты, цитаты и источники.
10. Не представляй предположения как факты.
11. Не выдавай старые события за сегодняшние.
12. Если материалов для раздела недостаточно, не заполняй его выдумками.
13. Сохраняй оригинальные ссылки на публикации.
14. Заголовки должны быть интересными, но не сенсационными.
15. Излагай содержание профессионально, понятно и содержательно.

Верни исключительно корректный JSON:

{{
  "date": "дата выпуска",
  "articles": [
    {{
      "category": "Название раздела",
      "title": "Заголовок",
      "summary": "Краткое содержание",
      "source": "Название источника",
      "url": "Ссылка на источник"
    }}
  ]
}}

Материалы:
{json.dumps(news, ensure_ascii=False)}
"""

import time

response = None

for attempt in range(5):
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt
        )
        break
    except Exception as error:
        print(f"Попытка {attempt + 1} не удалась: {error}")
        if attempt == 4:
            raise
        time.sleep(15 * (attempt + 1))

content = response.text.strip()
content = content.replace("```json", "").replace("```", "").strip()

edition = json.loads(content)

moscow_time = datetime.now(
    timezone(timedelta(hours=3))
)

edition["date"] = moscow_time.strftime("%d.%m.%Y")

os.makedirs("data", exist_ok=True)

with open("data/edition.json", "w", encoding="utf-8") as file:
    json.dump(edition, file, ensure_ascii=False, indent=2)

print("LEBEDEV TIME: выпуск подготовлен!")
print("Статей:", len(edition["articles"]))
