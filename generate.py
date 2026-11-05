import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from google import genai

API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

FEEDS = [
    "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru",
    "https://rss.dw.com/rdf/rss-ru-all",
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

            for item in root.findall(".//item")[:20]:
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
Ты — редакция ежедневной газеты LEBEDEV TIME.
Подготовь качественный русскоязычный выпуск на основе
ТОЛЬКО предоставленных новостных материалов.

Разделы:
1. Политика
2. Закон и право
3. Общество
4. Экономика
5. Технологии
6. Психология и поведение людей
7. Необычные истории и загадки

Правила:
- Не выдумывай события, факты, даты и цитаты.
- Не представляй предположения как факты.
- Используй только информацию из переданных материалов.
- Если материалов для раздела нет, не заполняй его выдумками.
- Пиши содержательно, профессионально и интересно.
- Сохраняй ссылки на исходные публикации.
- Не выдавай старые события за сегодняшние.

Верни исключительно корректный JSON следующего вида:
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

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents=prompt
)

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
