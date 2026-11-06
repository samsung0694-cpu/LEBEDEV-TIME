
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai


API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)

MODEL = "gemini-3.5-flash-lite"

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

MAX_NEWS = 60


def local_name(tag):
    return tag.split("}")[-1].lower()


def get_child_text(element, names):
    for child in element.iter():
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def valid_url(url, base_url=""):
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


class ArticleParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.image = ""
        self.ignored = 0
        self.content_depth = 0
        self.current_paragraph = None
        self.all_paragraphs = []
        self.main_paragraphs = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)

        if tag == "meta" and not self.image:
            name = (
                attrs.get("property")
                or attrs.get("name")
                or ""
            ).lower()

            if name in ("og:image", "twitter:image"):
                self.image = attrs.get("content", "")

        if tag in (
            "script", "style", "nav", "footer",
            "header", "aside", "noscript",
            "svg", "form"
        ):
            self.ignored += 1
            return

        if self.ignored:
            return

        if tag in ("article", "main"):
            self.content_depth += 1

        if tag == "p":
            self.current_paragraph = {
                "text": "",
                "main": self.content_depth > 0
            }

    def handle_data(self, data):
        if self.ignored or self.current_paragraph is None:
            return

        self.current_paragraph["text"] += data

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in (
            "script", "style", "nav", "footer",
            "header", "aside", "noscript",
            "svg", "form"
        ):
            self.ignored = max(0, self.ignored - 1)
            return

        if self.ignored:
            return

        if tag == "p" and self.current_paragraph:
            paragraph = re.sub(
                r"\s+",
                " ",
                self.current_paragraph["text"]
            ).strip()

            if len(paragraph) > 50:
                self.all_paragraphs.append(paragraph)

                if self.current_paragraph["main"]:
                    self.main_paragraphs.append(paragraph)

            self.current_paragraph = None

        if tag in ("article", "main"):
            self.content_depth = max(
                0,
                self.content_depth - 1
            )

    def get_text(self):
        paragraphs = (
            self.main_paragraphs
            if self.main_paragraphs
            else self.all_paragraphs
        )

        result = []
        seen = set()

        for paragraph in paragraphs:
            if paragraph not in seen:
                seen.add(paragraph)
                result.append(paragraph)

        return "\n\n".join(result)[:5000]


def image_from_description(description, base_url):
    if not description:
        return ""

    match = re.search(
        r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        description,
        re.IGNORECASE
    )

    if match:
        return valid_url(match.group(1), base_url)

    return ""


def image_from_feed_item(item, base_url):
    for element in item.iter():
        name = local_name(element.tag)

        if name in ("thumbnail", "content"):
            image = valid_url(
                element.attrib.get("url", ""),
                base_url
            )

            if image:
                return image

        if name == "enclosure":
            if element.attrib.get("type", "").startswith("image/"):
                image = valid_url(
                    element.attrib.get("url", ""),
                    base_url
                )

                if image:
                    return image

    return ""


def fetch_article_page(article_url):
    try:
        request = urllib.request.Request(
            article_url,
            headers=HEADERS
        )

        with urllib.request.urlopen(
            request,
            timeout=12
        ) as response:

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            if "html" not in content_type.lower():
                return "", ""

            page = response.read(700000).decode(
                "utf-8",
                errors="replace"
            )

        parser = ArticleParser()
        parser.feed(page)

        return (
            parser.get_text(),
            valid_url(parser.image, article_url)
        )

    except Exception as error:
        print("Ошибка загрузки статьи:", error)
        return "", ""



def collect_news():
    # Собираем новости отдельно по каждому источнику,
    # чтобы первая RSS-лента не вытесняла остальные.
    feeds_items = []

    for feed_url in FEEDS:
        current_feed = []

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

            # Не более 8 материалов из одной ленты.
            for entry in entries[:8]:
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

                link = valid_url(link, feed_url)

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
                    source = urlparse(link).netloc.replace(
                        "www.",
                        ""
                    )

                current_feed.append({
                    "title": html.unescape(title),
                    "link": link,
                    "description": html.unescape(description),
                    "source": source,
                    "image": image
                })

            print(
                f"Получено из источника: {len(current_feed)}"
            )

        except Exception as error:
            print(f"Ошибка RSS ({feed_url}): {error}")

        feeds_items.append(current_feed)

    # Чередуем источники: сначала по одной новости из каждой ленты,
    # затем второй круг и так далее.
    unique_items = []
    seen_links = set()
    max_per_feed = max(
        (len(feed) for feed in feeds_items),
        default=0
    )

    for position in range(max_per_feed):
        for feed in feeds_items:
            if position >= len(feed):
                continue

            item = feed[position]

            if item["link"] in seen_links:
                continue

            seen_links.add(item["link"])
            unique_items.append(item)

            if len(unique_items) >= 60:
                break

        if len(unique_items) >= 60:
            break

    print("Всего найдено уникальных материалов:", len(unique_items))
    print("Количество источников:", len(FEEDS))

    return unique_items



def prepare_article_material(item):
    text, page_image = fetch_article_page(item["link"])

    item["article_text"] = text

    if not item.get("image"):
        item["image"] = page_image

    return item


def generate_edition(news):
    materials = []

    print("Загрузка текстов первоисточников...")

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(prepare_article_material, item)
            for item in news
        ]

        for future in as_completed(futures):
            try:
                materials.append(future.result())
            except Exception as error:
                print("Ошибка обработки материала:", error)

    materials.sort(
        key=lambda item: news.index(
            next(
                original for original in news
                if original["link"] == item["link"]
            )
        )
    )

    usable_materials = []

    for item in materials:
        usable_materials.append({
            "title": item["title"],
            "url": item["link"],
            "source": item["source"],
            "description": item["description"][:1500],
            "article_text": item["article_text"][:5000]
        })

    prompt = f"""
Ты — главный редактор электронного журнала LEBEDEV TIME.

Создай ежедневный новостной выпуск на русском языке.

ОБЯЗАТЕЛЬНЫЕ РУБРИКИ:

1. Политика
2. Право
3. Общество
4. Экономика
5. Технологии
6. Психология
7. Необычные истории
8. Калининград

ТРЕБОВАНИЯ К ОТБОРУ:

ТРЕБОВАНИЯ К ОТБОРУ:

1. Подготовь выпуск из 15–20 наиболее значимых новостей,
если в исходных материалах достаточно информации.

2. Обязательно соблюдай географический баланс:

- Международные события — ориентир 3–5 материалов.
- Россия и федеральная повестка — ориентир 4–6 материалов.
- Калининград и Калининградская область — не более 2 материалов.
- Остальные материалы распределяй между тематическими рубриками.

3. Не допускай, чтобы новости одного региона или одного
источника занимали большую часть выпуска.

4. Международные новости должны касаться событий за пределами
России, если это подтверждается исходным материалом.

5. Российские новости должны отражать федеральные события,
а не только события Калининграда.

6. Для Калининграда используй только материалы
о Калининграде и Калининградской области.

7. Не дублируй новости.

8. Не выдавай старые события за новые.

9. Не выдумывай факты, даты, имена, цифры и цитаты.

10. Если материалов для какой-либо группы недостаточно,
не заполняй её вымышленными событиями.

11. Используй только переданные источники и сведения.

12. Не отдавай предпочтение новостям только потому,
что они находятся в начале списка.
ТРЕБОВАНИЯ К СТАТЬЯМ:

Для каждой выбранной новости подготовь:

1. title — оригинальный редакционный заголовок.
2. summary — краткий анонс из 2 предложений.
3. body — самостоятельную развёрнутую статью из 5–7 абзацев.

Объём body — ориентировочно 300–500 слов.

ПРАВИЛА НАПИСАНИЯ:

- Не копируй оригинальные статьи.
- Не заменяй слова синонимами, сохраняя чужую структуру.
- Используй проверенные факты, но строй собственное изложение.
- Объясняй предысторию, значение события и возможные последствия.
- Чётко отделяй установленные факты от оценок и предположений.
- Не создавай вымышленные цитаты и комментарии.
- Не добавляй выводы, которые не подтверждаются материалами.
- Не называй текст оригинальной журналистской работой очевидца.
- Не используй длинные фрагменты исходных публикаций.
- Если исходный материал содержит недостаточно информации,
не создавай искусственно длинную статью.
- Не придумывай дополнительные источники.

ВАЖНО:

Каждая статья должна относиться к конкретному материалу
из переданного списка.

Поле url должно в точности совпадать
с одной из переданных ссылок.

Поле source должно соответствовать первоисточнику.

Верни исключительно корректный JSON:

{{
  "date": "",
  "articles": [
    {{
      "category": "Политика",
  "region": "Мир",
  "title": "Заголовок",
  "summary": "Краткий анонс",
  "body": "Текст статьи",
  "source": "Название источника",
  "url": "Точная ссылка"
    }}
  ]
}}

МАТЕРИАЛЫ:

{json.dumps(usable_materials, ensure_ascii=False)}
"""

    response = None

    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt
            )
            break

        except Exception as error:
            print(
                f"Попытка {attempt + 1} не удалась: {error}"
            )

            if attempt == 4:
                raise

            time.sleep(15 * (attempt + 1))

      if not response or not response.text:
        raise RuntimeError("ИИ не вернул текст выпуска")

    content = response.text.strip()

    # Удаляем Markdown-обрамление
    if content.startswith("```"):
        content = content.replace("```json", "", 1)
        content = content.replace("```", "").strip()

    # Находим начало и конец JSON
    start = content.find("{")
    end = content.rfind("}")

    if start == -1 or end == -1:
        print("ОТВЕТ GEMINI:")
        print(content[:5000])
        raise RuntimeError("Gemini не вернул JSON")

    content = content[start:end + 1]

    try:
        edition = json.loads(content)

    except json.JSONDecodeError as error:
        print("ОШИБКА В JSON ОТ GEMINI")
        print(f"Строка: {error.lineno}, позиция: {error.colno}")
        print("Фрагмент ответа:")

        lines = content.splitlines()
        first = max(0, error.lineno - 4)
        last = min(len(lines), error.lineno + 3)

        for number in range(first, last):
            print(f"{number + 1}: {lines[number]}")

        raise

    if not isinstance(edition.get("articles"), list):
        raise RuntimeError("Некорректная структура выпуска")

    source_map = {
        item["link"]: item
        for item in materials
    }

    valid_articles = []

    for article in edition["articles"]:

        original = source_map.get(article.get("url", ""))

        if not original:
            print(
                "Пропущена статья с неизвестной ссылкой:",
                article.get("title", "")
            )
            continue

        article["source"] = original["source"]
        article["image"] = original.get("image", "")

        article["title"] = str(article.get("title", ""))[:250]
        article["summary"] = str(article.get("summary", ""))[:1500]
        article["body"] = str(article.get("body", ""))[:12000]

        if not article["title"] or not article["body"]:
            print(
                "Пропущена статья без полного текста:",
                article.get("title", "")
            )
            continue

        valid_articles.append(article)

    edition["articles"] = valid_articles

    return edition


def main():
    news = collect_news()

    if not news:
        raise RuntimeError(
            "Не удалось получить новости из RSS-лент"
        )

    edition = generate_edition(news)

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

    articles = edition.get("articles", [])

    print("LEBEDEV TIME: выпуск подготовлен!")
    print("Статей:", len(articles))
    print(
        "Статей с фотографиями:",
        sum(1 for article in articles if article.get("image"))
    )
    print(
        "Статей с полным текстом:",
        sum(1 for article in articles if article.get("body"))
    )


if __name__ == "__main__":
    main()
