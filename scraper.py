#!/usr/bin/env python3
"""
Latvijas Lauksaimniecības Ziņu RSS Feed Agregators

Apvieno ziņas no vairākiem Latvijas lauksaimniecības avotiem vienā RSS feedā.
Ģenerē docs/feed.xml, ko GitHub Pages publicē un Feedly var lasīt.
"""

import os
import re
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfigurācija
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 30  # sekundes
MAX_ARTICLES = 200
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "lv,en;q=0.5"}

# Latvisko mēnešu nosaukumi → skaitlis (datumu parsēšanai)
LV_MONTHS = {
    "janvāris": 1, "janvārī": 1, "janvāra": 1, "janvār": 1, "janv": 1,
    "februāris": 2, "februārī": 2, "februāra": 2, "febr": 2,
    "marts": 3, "martā": 3, "marta": 3, "mart": 3,
    "aprīlis": 4, "aprīlī": 4, "aprīļa": 4, "apr": 4,
    "maijs": 5, "maijā": 5, "maija": 5,
    "jūnijs": 6, "jūnijā": 6, "jūnija": 6, "jūn": 6,
    "jūlijs": 7, "jūlijā": 7, "jūlija": 7, "jūl": 7,
    "augusts": 8, "augustā": 8, "augusta": 8, "aug": 8,
    "septembris": 9, "septembrī": 9, "septembra": 9, "sept": 9,
    "oktobris": 10, "oktobrī": 10, "oktobra": 10, "okt": 10,
    "novembris": 11, "novembrī": 11, "novembra": 11, "nov": 11,
    "decembris": 12, "decembrī": 12, "decembra": 12, "dec": 12,
}

# ---------------------------------------------------------------------------
# Avotu saraksts
# ---------------------------------------------------------------------------
SOURCES = [
    # --- RSS feeds ---
    {
        "id": "lbla",
        "label": "LBLA",
        "type": "rss",
        "url": "https://www.lbla.lv/feed/",
        "page_url": "https://www.lbla.lv/",
    },
    {
        "id": "arei",
        "label": "AREI",
        "type": "rss",
        "url": "https://www.arei.lv/lv/rss.xml",
        "page_url": "https://www.arei.lv/lv/zinas",
    },
    {
        "id": "vaks",
        "label": "VAKS",
        "type": "rss",
        "url": "https://www.vaks.lv/feed/",
        "page_url": "https://www.vaks.lv/jaunumi-un-pasakumi/",
    },
    # --- HTML scrape ---
    {
        "id": "llkc",
        "label": "LLKC",
        "type": "scrape",
        "url": "https://llkc.lv/aktualitates/",
        "page_url": "https://llkc.lv/aktualitates/",
        "parser": "parse_llkc",
    },
    {
        "id": "scandagra",
        "label": "Scandagra",
        "type": "scrape",
        "url": "https://www.scandagra.lv/aktualit%C4%81tes/",
        "page_url": "https://www.scandagra.lv/aktualit%C4%81tes/",
        "parser": "parse_scandagra",
    },
    {
        "id": "linasagro",
        "label": "Linas Agro",
        "type": "scrape",
        "url": "https://www.linasagro.lv/agro-zinas",
        "page_url": "https://www.linasagro.lv/agro-zinas",
        "parser": "parse_linasagro",
    },
    {
        "id": "zemniekusaeima",
        "label": "Zemnieku Saeima",
        "type": "scrape",
        "url": "https://zemniekusaeima.lv/aktualitates/",
        "page_url": "https://zemniekusaeima.lv/aktualitates/",
        "parser": "parse_zemniekusaeima",
    },
    {
        "id": "lad",
        "label": "LAD",
        "type": "scrape",
        "url": "https://www.lad.gov.lv/lv/jaunumi",
        "page_url": "https://www.lad.gov.lv/lv/jaunumi",
        "parser": "parse_drupal_gov",
        "base_url": "https://www.lad.gov.lv",
    },
    {
        "id": "zm",
        "label": "ZM",
        "type": "scrape",
        "url": "https://www.zm.gov.lv/lv/jaunumi",
        "page_url": "https://www.zm.gov.lv/lv/jaunumi",
        "parser": "parse_drupal_gov",
        "base_url": "https://www.zm.gov.lv",
    },
    {
        "id": "vaad",
        "label": "VAAD",
        "type": "scrape",
        "url": "https://www.vaad.gov.lv/lv/jaunumi",
        "page_url": "https://www.vaad.gov.lv/lv/jaunumi",
        "parser": "parse_drupal_gov",
        "base_url": "https://www.vaad.gov.lv",
    },
    {
        "id": "saimnieks",
        "label": "Saimnieks.lv",
        "type": "scrape",
        "url": "https://www.saimnieks.lv/visi-jaunumi",
        "page_url": "https://www.saimnieks.lv/visi-jaunumi",
        "parser": "parse_saimnieks",
    },
    # --- Facebook lapas (nevar scrapot bez autentifikācijas) ---
    # mbasic.facebook.com atgriež login sienu visām publiskajām lapām.
    # Iespējamie risinājumi:
    #   1) Facebook Graph API ar access token (vajag admin piekļuvi lapai)
    #   2) Ārējs serviss, piemēram, Apify Facebook Pages Scraper
    #   3) RSS Bridge (self-hosted) ar Facebook bridge
    # Zemāk ir saraksts — ja atradīsiet risinājumu, mainiet type uz "rss"
    # un norādiet ārējā servisa URL laukā "url".
    {
        "id": "fb_agerona",
        "label": "Agerona Latvija",
        "type": "facebook",
        "url": "https://www.facebook.com/agerona.latvija/",
        "page_url": "https://www.facebook.com/agerona.latvija/",
        "fb_page_id": "agerona.latvija",
    },
    {
        "id": "fb_agricon",
        "label": "Agricon Latvija",
        "type": "facebook",
        "url": "https://www.facebook.com/agriconlatvija/",
        "page_url": "https://www.facebook.com/agriconlatvija/",
        "fb_page_id": "agriconlatvija",
    },
    {
        "id": "fb_agrochema",
        "label": "Agrochema",
        "type": "facebook",
        "url": "https://www.facebook.com/agrochema.lv/",
        "page_url": "https://www.facebook.com/agrochema.lv/",
        "fb_page_id": "agrochema.lv",
    },
    {
        "id": "fb_agtech",
        "label": "AgTech LV",
        "type": "facebook",
        "url": "https://www.facebook.com/AgTechLV/",
        "page_url": "https://www.facebook.com/AgTechLV/",
        "fb_page_id": "AgTechLV",
    },
    {
        "id": "fb_basf",
        "label": "BASF Agricultural Solutions",
        "type": "facebook",
        "url": "https://www.facebook.com/BASFAgriculturalSolutionsLV/",
        "page_url": "https://www.facebook.com/BASFAgriculturalSolutionsLV/",
        "fb_page_id": "BASFAgriculturalSolutionsLV",
    },
    {
        "id": "fb_bayer",
        "label": "Bayer CropScience Latvija",
        "type": "facebook",
        "url": "https://www.facebook.com/BayerCropScienceLatvija/",
        "page_url": "https://www.facebook.com/BayerCropScienceLatvija/",
        "fb_page_id": "BayerCropScienceLatvija",
    },
    {
        "id": "fb_eagronom",
        "label": "eAgronom Latvija",
        "type": "facebook",
        "url": "https://www.facebook.com/eAgronomLatvija/",
        "page_url": "https://www.facebook.com/eAgronomLatvija/",
        "fb_page_id": "eAgronomLatvija",
    },
    {
        "id": "fb_linasagro",
        "label": "Linas Agro (FB)",
        "type": "facebook",
        "url": "https://www.facebook.com/linasagro.lv/",
        "page_url": "https://www.facebook.com/linasagro.lv/",
        "fb_page_id": "linasagro.lv",
    },
    {
        "id": "fb_latraps",
        "label": "LATRAPS",
        "type": "facebook",
        "url": "https://www.facebook.com/LATRAPS/",
        "page_url": "https://www.facebook.com/LATRAPS/",
        "fb_page_id": "LATRAPS",
    },
    {
        "id": "fb_scandagra",
        "label": "Scandagra (FB)",
        "type": "facebook",
        "url": "https://www.facebook.com/scandagralatvia/",
        "page_url": "https://www.facebook.com/scandagralatvia/",
        "fb_page_id": "scandagralatvia",
    },
    {
        "id": "fb_lpksvaks",
        "label": "LPKS VAKS",
        "type": "facebook",
        "url": "https://www.facebook.com/LPKSVAKS/",
        "page_url": "https://www.facebook.com/LPKSVAKS/",
        "fb_page_id": "LPKSVAKS",
    },
]


# ---------------------------------------------------------------------------
# Utilītu funkcijas
# ---------------------------------------------------------------------------
def fetch_html(url: str) -> Optional[BeautifulSoup]:
    """Ielādē lapu un atgriež BeautifulSoup objektu."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        log.error("Neizdevās ielādēt %s: %s", url, e)
        return None


def parse_date_dmy_dot(text: str) -> Optional[datetime]:
    """Parsē datumu formātā DD.MM.YYYY. (ar vai bez beigu punkta)."""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                        tzinfo=timezone.utc)
    return None


def parse_date_dmy_slash(text: str) -> Optional[datetime]:
    """Parsē datumu formātā DD/MM/YYYY."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                        tzinfo=timezone.utc)
    return None


def parse_date_ymd_space(text: str) -> Optional[datetime]:
    """Parsē datumu formātā YYYY MM DD (ar atstarpēm)."""
    m = re.search(r"(\d{4})\s+(\d{1,2})\s+(\d{1,2})", text)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=timezone.utc)
    return None


def parse_date_lv(text: str) -> Optional[datetime]:
    """Parsē latvisko datumu, piem., '30 marts, 2026' vai '30. marts 2026'."""
    text_lower = text.lower().strip()
    # Noņemam komatus un punktus
    text_clean = re.sub(r"[.,]", " ", text_lower)
    parts = text_clean.split()
    if len(parts) < 3:
        return None
    # Mēģinām atrast dienu, mēnesi, gadu
    day = month_num = year = None
    for part in parts:
        if part.isdigit():
            n = int(part)
            if n > 31:
                year = n
            elif day is None:
                day = n
            else:
                year = n
        else:
            for lv_name, num in LV_MONTHS.items():
                if part.startswith(lv_name[:3]):
                    month_num = num
                    break
    if day and month_num and year:
        return datetime(year, month_num, day, tzinfo=timezone.utc)
    return None


def clean_html(text: str) -> str:
    """Notīra HTML tagus un lieko whitespace no teksta."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    clean = soup.get_text(separator=" ", strip=True)
    # Noņemam Drupal debug komentārus
    clean = re.sub(r"<!--.*?-->", "", clean, flags=re.DOTALL)
    return re.sub(r"\s+", " ", clean).strip()


def make_article(title: str, link: str, pub_date: Optional[datetime],
                 description: str, source_label: str, source_page_url: str) -> dict:
    """Izveido standartizētu raksta objektu ar avota prefiksu."""
    return {
        "title": f"[{source_label}] {title.strip()}",
        "link": link.strip(),
        "pub_date": pub_date or datetime.now(timezone.utc),
        "description": clean_html(description)[:500] if description else "",
        "source_page_url": source_page_url,
    }


# ---------------------------------------------------------------------------
# RSS feed parseri
# ---------------------------------------------------------------------------
def fetch_rss_source(source: dict) -> List[Dict]:
    """Ielādē un parsē RSS feedu, atgriež rakstu sarakstu."""
    log.info("  RSS: %s", source["url"])
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.error("  Neizdevās ielādēt RSS %s: %s", source["url"], e)
        return []

    feed = feedparser.parse(resp.content)
    articles = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue

        # Datums
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

        # Apraksts
        description = entry.get("summary", "") or entry.get("description", "")

        articles.append(make_article(
            title, link, pub_date, description,
            source["label"], source["page_url"],
        ))
    log.info("  → %d raksti no %s", len(articles), source["label"])
    return articles


# ---------------------------------------------------------------------------
# HTML scraperi
# ---------------------------------------------------------------------------

def parse_llkc(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """LLKC — WordPress + Breakdance builder."""
    articles = []
    for el in soup.select("article.ee-post"):
        link_tag = el.select_one("a.bde-container-link")
        if not link_tag or not link_tag.get("href"):
            continue
        link = link_tag["href"]

        title_tag = el.select_one("h2")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Datums — pirmais div ar tekstu DD/MM/YYYY formātā
        date_text = ""
        for div in el.select("div[class*='bde-text']"):
            txt = div.get_text(strip=True)
            if re.search(r"\d{1,2}/\d{1,2}/\d{4}", txt):
                date_text = txt
                break
        pub_date = parse_date_dmy_slash(date_text)

        # Apraksts — pēdējais bde-text div, kas nav datums
        desc = ""
        text_divs = el.select("div[class*='bde-text']")
        for div in text_divs:
            txt = div.get_text(strip=True)
            if txt and not re.search(r"^\d{1,2}/\d{1,2}/\d{4}$", txt):
                desc = txt

        if title:
            articles.append(make_article(
                title, link, pub_date, desc, source["label"], source["page_url"]
            ))
    return articles


def parse_scandagra(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """Scandagra — WordPress + Bootstrap."""
    articles = []
    wrapper = soup.select_one("div.news-list-wrap")
    if not wrapper:
        return articles
    for card in wrapper.select("a.item"):
        link = card.get("href", "")
        if not link:
            continue
        if not link.startswith("http"):
            link = urljoin(source["url"], link)

        title_tag = card.select_one("h3")
        title = title_tag.get_text(strip=True) if title_tag else ""

        date_tag = card.select_one("span.date")
        date_text = date_tag.get_text(strip=True) if date_tag else ""
        pub_date = parse_date_dmy_slash(date_text)

        desc_tag = card.select_one(".content p")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""

        if title:
            articles.append(make_article(
                title, link, pub_date, desc, source["label"], source["page_url"]
            ))
    return articles


def parse_linasagro(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """Linas Agro — OpenCart based."""
    articles = []
    for el in soup.select("div.new"):
        title_tag = el.select_one("a.title")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = title_tag.get("href", "")
        if link and not link.startswith("http"):
            link = urljoin("https://www.linasagro.lv", link)

        date_tag = el.select_one("div.date")
        date_text = date_tag.get_text(strip=True) if date_tag else ""
        pub_date = parse_date_ymd_space(date_text)

        desc_tag = el.select_one("div.short-text")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""

        if title:
            articles.append(make_article(
                title, link, pub_date, desc, source["label"], source["page_url"]
            ))
    return articles


def parse_zemniekusaeima(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """Zemnieku Saeima — WordPress + Elementor."""
    articles = []
    for el in soup.select("div.e-loop-item"):
        # Saite
        link_tag = el.select_one('a[href*="/aktualitate"]')
        if not link_tag:
            link_tag = el.select_one("a[href]")
        if not link_tag:
            continue
        link = link_tag.get("href", "")

        # Datums
        time_tag = el.select_one("time")
        date_text = time_tag.get_text(strip=True) if time_tag else ""
        pub_date = parse_date_lv(date_text)

        # Virsraksts — otrais post-info widgets vai h2/h3
        title = ""
        heading = el.select_one("h2, h3")
        if heading:
            title = heading.get_text(strip=True)
        else:
            post_infos = el.select(".elementor-widget-post-info")
            if len(post_infos) > 1:
                title = post_infos[1].get_text(strip=True)
            else:
                # Mēģinām atrast custom tipa elementu
                custom = el.select_one('[class*="item--type-custom"]')
                if custom:
                    title = custom.get_text(strip=True)

        if title:
            articles.append(make_article(
                title, link, pub_date, "", source["label"], source["page_url"]
            ))
    return articles


def parse_drupal_gov(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """LAD, ZM, VAAD — vienāda Drupal struktūra."""
    base_url = source.get("base_url", "")
    articles = []
    for el in soup.select("div.views-row"):
        title_tag = el.select_one("div.title h3 a")
        if not title_tag:
            title_tag = el.select_one("h3 a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = title_tag.get("href", "")
        if link and not link.startswith("http"):
            link = base_url + link

        date_tag = el.select_one("div.date")
        date_text = date_tag.get_text(strip=True) if date_tag else ""
        pub_date = parse_date_dmy_dot(date_text)

        desc_tag = el.select_one("div.text")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""

        if title:
            articles.append(make_article(
                title, link, pub_date, desc, source["label"], source["page_url"]
            ))
    return articles


def parse_saimnieks(soup: BeautifulSoup, source: dict) -> List[Dict]:
    """Saimnieks.lv — Custom Bootstrap."""
    articles = []
    for el in soup.select("div.news-item"):
        title_tag = el.select_one("a.post-title")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = title_tag.get("href", "")

        desc_tag = el.select_one("p.post-short")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""

        # Nav datuma saraksta lapā — izmantojam pašreizējo laiku
        articles.append(make_article(
            title, link, None, desc, source["label"], source["page_url"]
        ))
    return articles


# Parseru reģistrs — savieno parser nosaukumu ar funkciju
PARSERS = {
    "parse_llkc": parse_llkc,
    "parse_scandagra": parse_scandagra,
    "parse_linasagro": parse_linasagro,
    "parse_zemniekusaeima": parse_zemniekusaeima,
    "parse_drupal_gov": parse_drupal_gov,
    "parse_saimnieks": parse_saimnieks,
}


def fetch_scrape_source(source: dict) -> List[Dict]:
    """Ielādē HTML lapu un parsē ar atbilstošo parseri."""
    parser_name = source.get("parser")
    parser_func = PARSERS.get(parser_name)
    if not parser_func:
        log.error("  Nav atrasts parseris: %s", parser_name)
        return []

    log.info("  Scrape: %s", source["url"])
    soup = fetch_html(source["url"])
    if not soup:
        return []

    articles = parser_func(soup, source)
    log.info("  → %d raksti no %s", len(articles), source["label"])
    return articles


# ---------------------------------------------------------------------------
# Facebook — Apify Facebook Posts Scraper
# ---------------------------------------------------------------------------
# Lai izmantotu, nepieciešams:
#   1) Izveidot Apify kontu: https://apify.com/
#   2) Iegūt API tokenu: https://console.apify.com/account/integrations
#   3) Iestatīt kā vides mainīgo APIFY_API_TOKEN
#      (GitHub Actions: Settings → Secrets → APIFY_API_TOKEN)
#
# Izmanto Apify aktoru "apify/facebook-posts-scraper", kas scrapē
# publiskās Facebook lapas bez nepieciešamības pēc Facebook API tokena.
# ---------------------------------------------------------------------------

APIFY_ACTOR_ID = "apify/facebook-posts-scraper"
APIFY_API_BASE = "https://api.apify.com/v2"
APIFY_RUN_TIMEOUT = 120  # sekundes — max laiks Apify aktora izpildei
FB_POSTS_LIMIT = 10  # Cik ierakstus ielādēt no katras lapas


def get_apify_token() -> Optional[str]:
    """Atgriež Apify API tokenu no vides mainīgā."""
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token:
        return None
    return token


def fetch_facebook_source(source: dict) -> List[Dict]:
    """
    Ielādē ierakstus no Facebook lapas, izmantojot Apify Facebook Posts Scraper.

    Nepieciešams APIFY_API_TOKEN vides mainīgais.
    Ja token nav iestatīts, avots tiek izlaists ar brīdinājumu.
    """
    token = get_apify_token()
    if not token:
        log.warning(
            "  Facebook avots '%s' izlaists — nav iestatīts APIFY_API_TOKEN. "
            "Skatīt komentārus kodā par token iegūšanu.",
            source["label"],
        )
        return []

    page_id = source.get("fb_page_id", "")
    if not page_id:
        log.error("  Nav norādīts fb_page_id avotam: %s", source["id"])
        return []

    fb_url = source.get("url", f"https://www.facebook.com/{page_id}/")

    # Apify aktora ievaddati
    run_input = {
        "startUrls": [{"url": fb_url}],
        "resultsLimit": FB_POSTS_LIMIT,
    }

    # Palaižam aktoru sinhroni un saņemam rezultātus
    api_url = (
        f"{APIFY_API_BASE}/acts/{APIFY_ACTOR_ID}"
        f"/run-sync-get-dataset-items?token={token}"
    )

    log.info("  Apify Facebook scraper: %s", page_id)
    try:
        resp = requests.post(
            api_url,
            json=run_input,
            timeout=APIFY_RUN_TIMEOUT,
        )

        if resp.status_code == 402:
            log.error("  Apify konta limits sasniegts vai nav pietiekami kredīti.")
            return []

        if resp.status_code != 200:
            log.error(
                "  Apify API kļūda (HTTP %d): %s",
                resp.status_code,
                resp.text[:300],
            )
            return []

        posts = resp.json()
        if not isinstance(posts, list):
            log.error("  Negaidīts Apify atbildes formāts: %s", type(posts))
            return []

        if not posts:
            log.info("  → 0 ieraksti no %s", source["label"])
            return []

    except requests.exceptions.Timeout:
        log.error("  Apify pieprasījums pārsniedza laika limitu (%ds): %s",
                   APIFY_RUN_TIMEOUT, page_id)
        return []
    except Exception as e:
        log.error("  Neizdevās pieprasīt Apify API %s: %s", page_id, e)
        return []

    # Pārveidojam Apify rezultātus par rakstu objektiem
    articles = []
    for post in posts:
        # Teksts
        message = (post.get("text") or post.get("message") or "").strip()
        if not message:
            continue

        # Virsraksts — pirmais teikums vai pirmās 120 rakstzīmes
        title = _extract_fb_title(message)

        # Saite
        link = post.get("url") or post.get("postUrl") or ""
        if not link:
            post_id = post.get("postId") or post.get("id") or ""
            if post_id:
                link = f"https://www.facebook.com/{page_id}/posts/{post_id}"
        if not link:
            link = fb_url

        # Datums
        pub_date = None
        time_str = post.get("time") or post.get("timestamp") or ""
        if time_str:
            try:
                pub_date = datetime.fromisoformat(
                    str(time_str).replace("Z", "+00:00").replace("+0000", "+00:00")
                )
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        # Apraksts
        desc = message[:500]

        if title and link:
            articles.append(make_article(
                title, link, pub_date, desc,
                source["label"], source["page_url"],
            ))

    log.info("  → %d raksti no %s", len(articles), source["label"])
    return articles


def _extract_fb_title(text: str) -> str:
    """
    Izvelk virsrakstu no Facebook ieraksta teksta.
    Ņem pirmo teikumu vai pirmās 120 rakstzīmes.
    """
    # Noņemam URL no teksta (Facebook bieži ieliek saites)
    clean = re.sub(r"https?://\S+", "", text).strip()
    if not clean:
        clean = text.strip()

    # Pirmais teikums (beidzas ar . ! ? vai jaunas rindas)
    match = re.match(r"^(.+?)[.!?\n]", clean)
    if match and len(match.group(1)) >= 10:
        return match.group(1).strip()

    # Ja teikums ir pārāk īss vai nav atrasts, ņemam pirmos 120 chars
    if len(clean) <= 120:
        return clean
    # Griežam pie pēdējā atstarpes pirms 120 rakstzīmēm
    cut = clean[:120].rsplit(" ", 1)[0]
    return cut + "…"


# ---------------------------------------------------------------------------
# RSS XML ģenerēšana
# ---------------------------------------------------------------------------
def build_feed(articles: List[Dict], feed_url: str = "") -> str:
    """Ģenerē RSS 2.0 XML no rakstu saraksta."""
    fg = FeedGenerator()
    fg.title("Latvijas Lauksaimniecības Ziņas")
    fg.link(href=feed_url or "https://example.github.io/rss-reader/feed.xml",
            rel="self")
    fg.link(href="https://github.com", rel="alternate")
    fg.description(
        "Apkopotas ziņas no Latvijas lauksaimniecības avotiem: "
        "LLKC, LBLA, AREI, LAD, ZM, Scandagra, Linas Agro, Saimnieks.lv u.c."
    )
    fg.language("lv")
    fg.lastBuildDate(datetime.now(timezone.utc))

    # Sakārtojam pēc datuma (jaunākie pirmie), ierobežojam skaitu
    sorted_articles = sorted(
        articles,
        key=lambda a: a["pub_date"],
        reverse=True,
    )[:MAX_ARTICLES]

    for art in sorted_articles:
        fe = fg.add_entry()
        fe.title(art["title"])
        fe.link(href=art["link"])
        fe.guid(art["link"], permalink=True)
        fe.pubDate(art["pub_date"])
        if art["description"]:
            fe.description(art["description"])
        # Pievienojam avota lapu kā source elementu
        fe.source(title=art["title"].split("]")[0].strip("["),
                  url=art["source_page_url"])

    return fg.rss_str(pretty=True).decode("utf-8")


# ---------------------------------------------------------------------------
# Galvenā funkcija
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("Sākam RSS feed atjaunināšanu — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    all_articles = []
    errors = []

    # Apstrādājam katru avotu
    for source in SOURCES:
        source_type = source["type"]
        source_id = source["id"]
        try:
            log.info("Apstrādājam: [%s] %s", source["label"], source_type)
            if source_type == "rss":
                articles = fetch_rss_source(source)
            elif source_type == "scrape":
                articles = fetch_scrape_source(source)
            elif source_type == "facebook":
                articles = fetch_facebook_source(source)
            else:
                log.warning("  Nezināms avota tips: %s", source_type)
                articles = []
            all_articles.extend(articles)
        except Exception as e:
            log.error("KĻŪDA apstrādājot %s: %s\n%s", source_id, e,
                      traceback.format_exc())
            errors.append(source_id)

    log.info("-" * 60)
    apify_token = get_apify_token()
    active_sources = len([
        s for s in SOURCES
        if s["type"] != "facebook" or apify_token
    ])
    log.info("Kopā: %d raksti no %d avotiem (%d kļūdas)",
             len(all_articles), active_sources, len(errors))

    if not all_articles:
        log.warning("Nav neviena raksta! Pārbaudiet avotu pieejamību.")
        # Ja feed.xml jau eksistē, neatjauninām to ar tukšu failu
        output_path = Path(__file__).parent / "docs" / "feed.xml"
        if output_path.exists():
            log.info("Saglabājam esošo feed.xml")
            return
        # Ja arī feed.xml neeksistē, ģenerējam tukšu feedu
        log.info("Ģenerējam tukšu feedu")

    # Ģenerējam XML
    # Pielāgojiet šo URL jūsu GitHub Pages URL
    feed_url = os.environ.get(
        "FEED_URL",
        "https://oskarsozols.github.io/AgroRSS/feed.xml",
    )
    xml_content = build_feed(all_articles, feed_url)

    # Saglabājam failu
    output_path = Path(__file__).parent / "docs" / "feed.xml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_content, encoding="utf-8")
    log.info("Feed saglabāts: %s (%d baiti)", output_path, len(xml_content))

    if errors:
        log.warning("Kļūdainie avoti: %s", ", ".join(errors))

    log.info("Gatavs!")


if __name__ == "__main__":
    main()
