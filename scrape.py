import os
import json
import hashlib
import requests
import qrcode
import certifi
import time

from bs4 import BeautifulSoup
from urllib.parse import (
    urlparse,
    parse_qsl,
    urlencode,
    urlunparse,
    urljoin
)
from datetime import datetime
from email.utils import format_datetime
# from email.utils import parsedate_to_datetime
from xml.sax.saxutils import escape
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.unimed.coop.br"
NEWS_URL = "https://www.unimed.coop.br/site/web/aracatuba/noticias"

FINGERPRINT_FILE = ".fingerprint"

REPO = os.getenv("GITHUB_REPOSITORY", "local/local")
GITHUB_PAGES_URL = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}"

QR_FOLDER = "qr"
IMG_FOLDER = "img"

os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs(IMG_FOLDER, exist_ok=True)

MONTHS = {
    "Janeiro": 1,
    "Fevereiro": 2,
    "Março": 3,
    "Abril": 4,
    "Maio": 5,
    "Junho": 6,
    "Julho": 7,
    "Agosto": 8,
    "Setembro": 9,
    "Outubro": 10,
    "Novembro": 11,
    "Dezembro": 12
}

def get_hash_from_filename(filename):
    """
    Strips the file extension and any '-hq' tags to extract the core MD5 hash.
    Example: 'abc123xyz-hq.png' -> 'abc123xyz'
    """
    file_base_name, _ = os.path.splitext(filename)
    return file_base_name.replace("-hq", "")


def find_existing_file(folder, exact_base_name):
    """
    Checks if a file with the exact base name exists in the folder, 
    ignoring its extension. Returns the full filename if found, else None.
    Example: If looking for 'abc123xyz-hq', it matches 'abc123xyz-hq.webp'
    """
    if not os.path.exists(folder):
        return None
        
    for filename in os.listdir(folder):
        file_base_name, _ = os.path.splitext(filename)
        if file_base_name == exact_base_name:
            full_path = os.path.join(folder, filename)
            # Ensure it's a valid file and not corrupted (0 bytes)
            if os.path.getsize(full_path) > 0:
                return filename
    return None


def parse_date(date_text):
    parts = date_text.replace(" de ", " ").split()

    day = int(parts[0])
    month = MONTHS[parts[1]]
    year = int(parts[2])

    dt = datetime(year, month, day, 12, 0, 0)

    return format_datetime(dt)


def xml_safe(text):
    return escape(text, {"\"": "&quot;"})


def generate_qr(article_url):
    article_hash = hashlib.md5(
        article_url.encode()
    ).hexdigest()

    filename = f"{article_hash}.png"

    filepath = os.path.join(
        QR_FOLDER,
        filename
    )

    if (not os.path.exists(filepath) or os.path.getsize(filepath) == 0):
        img = qrcode.make(article_url)
        img.save(filepath)

    qr_url = (
        f"{GITHUB_PAGES_URL}/qr/{filename}"
    )

    return qr_url


def remove_image_preview(url):

    parsed = urlparse(url)

    query = parse_qsl(
        parsed.query,
        keep_blank_values=True
    )

    query = [
        (k, v)
        for k, v in query
        if k != "imagePreview"
    ]

    new_query = urlencode(query)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        )
    )


def get_session():
    session = requests.Session()

    retry = Retry(
        total=None,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def download_file(url, filepath):

    try:
        session = get_session()

        response = session.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
            },
            verify=False
        )

        response.raise_for_status()
        
    except requests.exceptions.RequestException as e:
        # Catches Timeouts, ConnectionErrors, DNS drops, etc.
        print(f"Network error occurred: {e}. Retrying anyway...")

    with open(filepath, "wb") as f:
        f.write(response.content)


def process_images(article_url, image_url):
    """
    Verifies if normal and HQ images exist under any extension. 
    Downloads them if missing, and returns the correct GitHub Pages URLs.
    """
    image_hash = hashlib.md5(article_url.encode()).hexdigest()
    
    # 1. Check if files already exist with ANY extension
    existing_normal = find_existing_file(IMG_FOLDER, image_hash)
    existing_hq = find_existing_file(IMG_FOLDER, f"{image_hash}-hq")
    
    # 2. Handle Normal Image
    if existing_normal:
        normal_filename = existing_normal
    else:
        normal_filename = f"{image_hash}.jpg"  # Default fallback extension
        download_file(image_url, os.path.join(IMG_FOLDER, normal_filename))
        
    # 3. Handle HQ Image
    hq_url = remove_image_preview(image_url)
    if existing_hq:
        hq_filename = existing_hq
    else:
        hq_filename = f"{image_hash}-hq.jpg"   # Default fallback extension
        download_file(hq_url, os.path.join(IMG_FOLDER, hq_filename))

    # 4. Return the exact asset paths matching the real extensions
    return {
        "image": f"{GITHUB_PAGES_URL}/img/{normal_filename}",
        "image_hq": f"{GITHUB_PAGES_URL}/img/{hq_filename}"
    }


def scrape():
    try:
        session = get_session()

        response = session.get(
            NEWS_URL,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
            },
            verify=False
        )
        
        if response.status_code == 200:
            print("Target reached successfully!")
        
    except requests.exceptions.RequestException as e:
        # Catches Timeouts, ConnectionErrors, DNS drops, etc.
        print(f"Network error occurred: {e}. Retrying anyway...")

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    cards = soup.select(".card-noticias")

    if len(cards) == 0:
        raise Exception(
            "No news cards found."
        )

    articles = []

    for card in cards:

        a = card.select_one("a")

        title = card.select_one(
            ".titulo-card-noticias"
        ).get_text(strip=True)

        subtitle = card.select_one(
            ".texto-card-noticias"
        ).get_text(strip=True)

        date = card.select_one(
            ".data-card-noticias"
        ).get_text(strip=True)

        article_url = urljoin(
            BASE_URL,
            a["href"]
        )

        image_url = urljoin(
            BASE_URL,
            card.select_one("img")["src"]
        )

        qr_url = generate_qr(
            article_url
        )

        images = process_images(
            article_url,
            image_url
        )

        articles.append({
            "title": title,
            "subtitle": subtitle,
            "date": parse_date(date),
            "url": article_url,
            "image": images["image"],
            "image_hq": images["image_hq"],
            "qr": qr_url
        })

    return articles


def save_json(data):
    with open(
        "feed.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def save_rss(data):

    items_xml = []

    for item in data:

        title = escape(
            item["title"]
        )

        description = item[
            "subtitle"
        ]

        rss_item = f"""
<item>
<title>{title}</title>

<link>{xml_safe(item['url'])}</link>

<description><![CDATA[{description}]]></description>

<pubDate>{item['date']}</pubDate>

<guid isPermaLink="true">{xml_safe(item['url'])}</guid>

<enclosure
    url="{xml_safe(item['image'])}"
    type="image/jpeg" />

<qr>{xml_safe(item['qr'])}</qr>

</item>
"""

        items_xml.append(
            rss_item
        )

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>

<rss version="2.0">

<channel>

<title>Unimed Araçatuba Notícias</title>

<link>{NEWS_URL}</link>

<description>
Últimas notícias da Unimed Araçatuba
</description>

{''.join(items_xml)}

</channel>

</rss>
"""

    with open(
        "feed.xml",
        "w",
        encoding="utf-8"
    ) as f:

        f.write(rss)


def fingerprint(data):

    normalized = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False
    )

    return hashlib.md5(
        normalized.encode()
    ).hexdigest()


def cleanup_files(articles):
    """
    Deletes files in IMG_FOLDER and QR_FOLDER that do not correspond 
    to the hashes of active articles, independent of their extension or -hq suffix.
    """
    # Create a set of all valid hashes from active articles
    valid_hashes = {
        hashlib.md5(article["url"].encode()).hexdigest() 
        for article in articles
    }

    # Clean up target folders
    for folder in [IMG_FOLDER, QR_FOLDER]:
        if not os.path.exists(folder):
            continue
            
        for filename in os.listdir(folder):
            file_hash = get_hash_from_filename(filename)
            
            if file_hash not in valid_hashes:
                os.remove(os.path.join(folder, filename))


def main():

    final_data = scrape()
    
    # final_data.sort(
    #     key=lambda x: parsedate_to_datetime(x["date"]),
    #     reverse=True
    # )

    new_fp = fingerprint(
        final_data
    )

    old_fp = ""

    if os.path.exists(
        FINGERPRINT_FILE
    ):
        old_fp = open(
            FINGERPRINT_FILE,
            "r"
        ).read()

    if new_fp == old_fp:
        print(
            "No content changes."
        )
        return

    cleanup_files(
        final_data
    )

    save_json(final_data)

    save_rss(final_data)
    
    with open(
        FINGERPRINT_FILE,
        "w"
    ) as f:

        f.write(new_fp)

    print(
        "Feed updated."
    )


if __name__ == "__main__":
    main()
