
import os
import re
import json
import time
import html
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta
from google import genai


API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)


FEEDS = [
    "https://www.newkaliningrad.ru/news/rss.xml",
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


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LEBEDEV-TIME/1.0)"
}


def local_name(tag):
    return tag.split("}")[-1].lower()


def get_child_text(element, names):
    for child in element.iter():
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def valid_image_url(url, base_url=""):
    if not url:
        return ""

    url = html.unescape(url.strip())

    if url.startswith("//"):
        url = "https:" + url
    elif base_url:
        url = urljoin(base_url, url)

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return ""

    return url


class ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta" or self.image:
            return

        attrs = dict(attrs)

        property_name = (
            attrs.get("property")
            or attrs.get("name")
            or ""
        ).lower()

        if property_name in ("og:image", "twitter:image"):
            self.image = attrs.get("content", "")


def image_from_html(source_html, base_url):
    if not source_html:
        return ""

    parser = ImageParser()

    try:
        parser.feed(source_html)
        return valid_image_url(parser.image, base_url)
    except Exception:
        return ""


def image_from_description(description, base_url):
    if not description:
        return ""

    match = re.search(
        r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        description,
        re.IGNORECASE
    )

    if match:
        return valid_image_url(match.group(1), base_url)

    return ""


def image_from_feed_item(item, base_url):
    for element in item.iter():
        name = local_name(element.tag)

        if name in ("thumbnail", "content"):
            image = valid_image_url(
                element.attrib.get("url", ""),
                base_url
            )

            if image:
                return image

        if name == "enclosure":
            mime_type = element.attrib.get("type", "")

            if mime_type.startswith("image/"):
                image = valid_image_url(
                    element.attrib.get("url", ""),
                    base_url
                )

                if image:
                    return image

    return ""


def image_from_article_page(article_url):
    try:
        request = urllib.request.Request(
            article_url,
            headers=HEADERS
        )

        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "")

            if "html" not in content_type.lower():
                return ""

            page = response.read(500000).decode(
                "utf-8",
                errors="replace"
            )

        return image_from_html(page, article_url)

    except Exception as error:
        print(f"Image page error: {error}")
        return ""


def collect_news():
    items = []

    for feed_url in FEEDS:
        try:
            print("Загрузка RSS:", feed_url)

            request = urllib.request.Request(
                feed_url,
                headers=HEADERS
            )

            with urllib.request.urlopen(
                request,
                timeout=25
            ) as response:
                root = ET.fromstring(response.read())

            entries = [
                element
                for element in root.iter()
                if local_name(element.tag) in ("item", "entry")
            ]

            for entry in entries[:50]:

                title = get_child_text(entry, {"title"})

                link = ""

                for element in entry.iter():
                    if local_name(element.tag) == "link":
                        link = (
                            element.attrib.get("href", "")
                            or element.text
                            or ""
                        ).strip()

                        if link:
                            break

                description = get_child_text(
                    entry,
                    {"description", "summary", "encoded", "content"}
                )

                link = valid_image_url(link, feed_url)

                if not title or not link:
                    continue

                image = image_from_feed_item(entry, link)

                if not image:
                    image = image_from_description(
                        description,
                        link
                    )

                source = get_child_text(entry, {"source"})

                if not source:
                    source = urlparse(link).netloc.replace("www.", "")

                items.append({
                    "title": html.unescape(title),
                    "link": link,
                    "description": html.unescape(description),
                    "source": source,
                    "image": image
                })

        except Exception as error:
            print(f"RSS error ({feed_url}): {error}")

    # Убираем повторяющиеся публикации
    unique_items = []
    seen_links = set()

    for item in items:
        if item["link"] not in seen_links:
            seen_links.add(item["link"])
            unique_items.append(item)

    print("Всего найдено новостей:", len(unique_items))

    return unique_items


news = collect_news()

if not news:
    raise RuntimeError(
        "Не удалось получить новости из RSS-лент"
    )


prompt = f"""
Ты — главный редактор ежедневного электронного журнала LEBEDEV TIME.

Подготовь полноценный ежедневный новостной выпуск на русском языке.

ОБЯЗАТЕЛЬНЫЕ РУБРИКИ:

1. Политика
2. Право
3. Общество
4. Экономика
5. Технологии
6. Психология
7. Необычные истории
8. Калининград

РЕДАКЦИОННЫЕ ПРАВИЛА:

1. Используй только предоставленные материалы.
2. Стремись включить минимум 2 статьи в каждую рубрику,
если для этого есть реальные материалы.
3. Не выдумывай события, факты, даты, цитаты и источники.
4. Не дублируй одну новость в нескольких рубриках.
5. Для рубрики «Право» выбирай законодательство,
судебную практику и правоохранительную деятельность.
6. Для рубрики «Психология» выбирай исследования,
поведение людей и социальные явления.
7. Для рубрики «Необычные истории» выбирай
документированные необычные события и открытия.
8. Для рубрики «Калининград» используй новости
Калининграда и Калининградской области.
9. Не выдавай старые события за сегодняшние.
10. Если материалов недостаточно, не выдумывай новости.
11. Сохраняй оригинальные ссылки и названия источников.
12. Пиши интересно, профессионально и понятно.

Верни исключительно корректный JSON следующего вида:

{{
  "date": "дата выпуска",
  "articles": [
    {{
      "category": "Название рубрики",
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


if not response or not response.text:
    raise RuntimeError("ИИ не вернул текст выпуска")


content = response.text.strip()
content = content.replace("```json", "").replace("```", "").strip()

edition = json.loads(content)


# Сопоставляем новости с исходными RSS-материалами
source_map = {
    item["link"]: item
    for item in news
}


for article in edition.get("articles", []):

    original = source_map.get(article.get("url", ""))

    image = ""

    if original:
        image = original.get("image", "")

        if not article.get("source"):
            article["source"] = original.get("source", "")

    # Если в RSS фотографии нет, ищем её
    # непосредственно на странице публикации
    if not image and article.get("url"):
        image = image_from_article_page(article["url"])

    article["image"] = image

    if image:
        print("Найдена фотография:", article.get("title", ""))
    else:
        print("Фотография не найдена:", article.get("title", ""))


moscow_time = datetime.now(
    timezone(timedelta(hours=3))
)

edition["date"] = moscow_time.strftime("%d.%m.%Y")


os.makedirs("data", exist_ok=True)

with open(
    "data/edition.json",
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        edition,
        file,
        ensure_ascii=False,
        indent=2
    )


print("LEBEDEV TIME: выпуск подготовлен!")
print("Статей:", len(edition.get("articles", [])))
print(
    "Статей с фотографиями:",
    sum(1 for article in edition.get("articles", []) if article.get("image"))
)
