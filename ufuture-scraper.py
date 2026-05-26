"""
UiTM Forum Scraper + Telegram Notifier (Docker-ready)
Uses environment variables for credentials
"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import socket
import time
import sqlite3
from datetime import datetime, timedelta
import json
import os
import sys
import html
import hashlib
import secrets
from requests import RequestException

# Fix Python 3.12+ deprecation warning
sqlite3.register_adapter(datetime, lambda val: val.isoformat() if val else None)

# ============================================================================
# CONFIGURATION FROM ENVIRONMENT VARIABLES
# ============================================================================

BASE_URL = "https://ufuture.uitm.edu.my"
LOGIN_URL = f"{BASE_URL}/login"
DASHBOARD_URL = f"{BASE_URL}/"

# Get credentials from environment
USERNAME = os.getenv("UFUTURE_USERNAME")
PASSWORD = os.getenv("UFUTURE_PASSWORD")
ROLE = "1"

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# Paths (Docker-friendly)
DB_FILE = os.getenv("DB_PATH", "/app/data/ufuture_forums.db")
SNAPSHOT_FILE = os.getenv("SNAPSHOT_PATH", "/app/data/telegram_last_snapshot.json")
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
REQUEST_RETRIES = int(os.getenv("REQUEST_RETRIES", "3"))
REQUEST_BACKOFF_SECONDS = float(os.getenv("REQUEST_BACKOFF_SECONDS", "5"))
SCRAPE_TOPIC_DETAILS = os.getenv("SCRAPE_TOPIC_DETAILS", "true").strip().lower() in {"1", "true", "yes", "on"}
TOPIC_DETAIL_LIMIT = int(os.getenv("TOPIC_DETAIL_LIMIT", "20"))
INCLUDE_REPLY_EXCERPTS = os.getenv("INCLUDE_REPLY_EXCERPTS", "true").strip().lower() in {"1", "true", "yes", "on"}
REPLY_EXCERPT_LENGTH = int(os.getenv("REPLY_EXCERPT_LENGTH", "160"))
DETAIL_EXCERPT_LENGTH = int(os.getenv("DETAIL_EXCERPT_LENGTH", "700"))
TELEGRAM_MAX_TOPICS_PER_MESSAGE = int(os.getenv("TELEGRAM_MAX_TOPICS_PER_MESSAGE", "8"))
TELEGRAM_INLINE_BUTTONS = os.getenv("TELEGRAM_INLINE_BUTTONS", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_DEDUP_REPLIES = os.getenv("TELEGRAM_DEDUP_REPLIES", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_SHORT_NAMES = os.getenv("TELEGRAM_SHORT_NAMES", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_MEDIA_BUTTONS = os.getenv("TELEGRAM_MEDIA_BUTTONS", "true").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_BUTTON_MODE = os.getenv("TELEGRAM_BUTTON_MODE", "hybrid").strip().lower()
TELEGRAM_ACTION_TTL_HOURS = int(os.getenv("TELEGRAM_ACTION_TTL_HOURS", "72"))
TELEGRAM_REPLY_DRAFT_TTL_HOURS = int(os.getenv("TELEGRAM_REPLY_DRAFT_TTL_HOURS", "24"))
TELEGRAM_INTERACTIVE_BOT = os.getenv("TELEGRAM_INTERACTIVE_BOT", "true").strip().lower() in {"1", "true", "yes", "on"}
UFUTURE_POST_REPLIES = os.getenv("UFUTURE_POST_REPLIES", "false").strip().lower() in {"1", "true", "yes", "on"}

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_TEMPORARY_FAILURE = 75

# Create directories if they don't exist
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/109.0 Safari/537.36",
    "Referer": LOGIN_URL,
}


class ScraperError(Exception):
    exit_code = EXIT_FAILURE


class ConfigError(ScraperError):
    exit_code = EXIT_CONFIG


class TemporaryNetworkError(ScraperError):
    exit_code = EXIT_TEMPORARY_FAILURE

# ============================================================================
# VALIDATION
# ============================================================================

def validate_config():
    """Validate that required environment variables are set"""
    errors = []
    
    if not USERNAME:
        errors.append("UFUTURE_USERNAME not set")
    if not PASSWORD:
        errors.append("UFUTURE_PASSWORD not set")
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN not set")
    if not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID not set")
    
    if errors:
        details = "\n".join(f"   - {error}" for error in errors)
        raise ConfigError(
            "Configuration errors:\n"
            f"{details}\n\n"
            "Set these environment variables before running the scraper."
        )

# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY,
        course_code TEXT UNIQUE,
        course_name TEXT,
        forum_url TEXT,
        last_updated TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS weeks (
        id INTEGER PRIMARY KEY,
        course_code TEXT,
        week_title TEXT,
        week_link TEXT,
        topics TEXT,
        posts TEXT,
        latest_user TEXT,
        latest_time TEXT,
        last_updated TIMESTAMP,
        FOREIGN KEY(course_code) REFERENCES courses(course_code),
        UNIQUE(course_code, week_title)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY,
        course_code TEXT,
        week_title TEXT,
        topic_id TEXT,
        topic_title TEXT,
        topic_url TEXT,
        description TEXT,
        created_by TEXT,
        created_at TEXT,
        replies INTEGER,
        last_activity TEXT,
        status TEXT,
        last_updated TIMESTAMP,
        FOREIGN KEY(course_code) REFERENCES courses(course_code),
        UNIQUE(course_code, week_title, topic_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY,
        topic_id TEXT,
        post_id TEXT,
        parent_post_id TEXT,
        post_level INTEGER,
        replyable INTEGER,
        author TEXT,
        content_excerpt TEXT,
        media_url TEXT,
        media_label TEXT,
        posted_time TEXT,
        last_updated TIMESTAMP,
        UNIQUE(topic_id, post_id)
    )''')

    ensure_column(c, "posts", "media_url", "TEXT")
    ensure_column(c, "posts", "media_label", "TEXT")
    ensure_column(c, "posts", "post_level", "INTEGER DEFAULT 0")
    ensure_column(c, "posts", "replyable", "INTEGER DEFAULT 0")

    c.execute('''CREATE TABLE IF NOT EXISTS telegram_actions (
        action_id TEXT PRIMARY KEY,
        action_type TEXT NOT NULL,
        chat_id TEXT,
        message_id TEXT,
        course_code TEXT,
        week_title TEXT,
        topic_id TEXT,
        post_id TEXT,
        topic_url TEXT,
        media_url TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        payload_json TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS telegram_pending_replies (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        topic_id TEXT NOT NULL,
        post_id TEXT,
        topic_url TEXT,
        source_action_id TEXT,
        target_post_id TEXT,
        target_author TEXT,
        target_excerpt TEXT,
        target_posted_time TEXT,
        target_label TEXT,
        topic_title TEXT,
        draft_text TEXT,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )''')
    ensure_column(c, "telegram_pending_replies", "topic_url", "TEXT")
    ensure_column(c, "telegram_pending_replies", "source_action_id", "TEXT")
    ensure_column(c, "telegram_pending_replies", "target_post_id", "TEXT")
    ensure_column(c, "telegram_pending_replies", "target_author", "TEXT")
    ensure_column(c, "telegram_pending_replies", "target_excerpt", "TEXT")
    ensure_column(c, "telegram_pending_replies", "target_posted_time", "TEXT")
    ensure_column(c, "telegram_pending_replies", "target_label", "TEXT")
    ensure_column(c, "telegram_pending_replies", "submitted_at", "TEXT")
    ensure_column(c, "telegram_pending_replies", "result_message", "TEXT")
    ensure_column(c, "telegram_pending_replies", "dedupe_key", "TEXT")

    c.execute('''CREATE TABLE IF NOT EXISTS telegram_bot_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    conn.commit()
    return conn


def ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cursor.fetchall()}
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

# ============================================================================
# SCRAPING FUNCTIONS
# ============================================================================

def safe_soup(text):
    return BeautifulSoup(text or "", "html.parser")


def is_name_resolution_error(error):
    text = str(error)
    return (
        "NameResolutionError" in text
        or "Temporary failure in name resolution" in text
        or "Failed to resolve" in text
    )


def is_retryable_request_error(error):
    if is_name_resolution_error(error):
        return True
    response = getattr(error, "response", None)
    if response is not None and response.status_code in {429, 500, 502, 503, 504}:
        return True
    return isinstance(error, requests.Timeout) or isinstance(error, requests.ConnectionError)


def request_with_retries(session, method, url, *, context, **kwargs):
    attempts = max(1, REQUEST_RETRIES)
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except RequestException as error:
            last_error = error
            print(f"{context} failed (attempt {attempt}/{attempts}): {error}")
            if attempt >= attempts or not is_retryable_request_error(error):
                break
            time.sleep(REQUEST_BACKOFF_SECONDS * attempt)

    if last_error and is_name_resolution_error(last_error):
        host = urlparse(url).hostname or url
        raise TemporaryNetworkError(
            f"{context} failed because DNS could not resolve {host}. "
            "Check TrueNAS/Docker DNS, gateway, and upstream resolver settings. "
            f"Original error: {last_error}"
        )

    raise ScraperError(f"{context} failed after {attempts} attempt(s): {last_error}")


def verify_dns(host):
    attempts = max(1, REQUEST_RETRIES)
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            address = socket.gethostbyname(host)
            print(f"DNS resolved {host} -> {address}")
            return
        except OSError as error:
            last_error = error
            print(f"DNS lookup failed for {host} (attempt {attempt}/{attempts}): {error}")
            if attempt < attempts:
                time.sleep(REQUEST_BACKOFF_SECONDS * attempt)

    raise TemporaryNetworkError(
        f"DNS lookup failed for {host}. "
        "This is a container/network problem, not a forum parsing problem. "
        f"Original error: {last_error}"
    )

def find_course_cards(soup):
    selectors = [
        "#contentUitm .card",
        "#contentUitm .card.m-b-20",
        ".col-sm-4 .card",
        ".hovercard",
        ".card.hovercard",
    ]
    for sel in selectors:
        cards = soup.select(sel)
        if cards:
            return cards
    return []

def extract_course_from_card(card):
    title_el = card.select_one(".title b") or card.select_one(".title h5") or card.select_one(".title")
    link_el = card.select_one('a.viewbadge') or card.select_one('a[href*="/contents/index/t:c/cid:"]') or card.select_one('a[href*="/contents/index/cid:"]')

    title = title_el.get_text(strip=True) if title_el else None
    href = link_el.get("href") if link_el else None
    view_url = urljoin(BASE_URL, href) if href else None

    return title, view_url

def find_forum_link_on_course_page(soup):
    anchors = soup.select('a[href*="/forumsv2/lobby/index/"]')
    pattern = re.compile(r"^/forumsv2/lobby/index/[^/]+/[^/]+")
    for a in anchors:
        href = a.get("href", "")
        if pattern.search(href):
            return href
    if anchors:
        anchors_sorted = sorted(anchors, key=lambda x: len(x.get("href","")), reverse=True)
        return anchors_sorted[0].get("href")
    return None

def extract_forum_weeks(soup):
    rows = soup.select("#academicTbl tbody tr")
    if not rows:
        rows = soup.select("table tbody tr")
    weeks = []
    for row in rows:
        title_element = row.select_one(".forumtitle") or row.select_one("a.forumtitle") or row.select_one("a")
        if not title_element:
            continue
        week_title = title_element.get_text(strip=True)
        week_href = title_element.get("href") or ""
        week_link = urljoin(BASE_URL, week_href)
        stats = row.select_one(".stats-wrapper")
        topics = posts = "0"
        if stats:
            s = stats.get_text("\n", strip=True).split("\n")
            topics = s[0] if len(s) > 0 else topics
            posts = s[1] if len(s) > 1 else posts
        last = row.select_one(".last-wrapper")
        latest_user = "No activity"
        if last:
            parts = last.get_text("\n", strip=True).split("\n")
            latest_user = parts[1] if len(parts) > 1 else parts[0]
            latest_user = latest_user.replace("...", "").replace(" …", "").strip()
        time_el = row.select_one(".forum-time")
        latest_time = time_el.get_text(strip=True) if time_el else "-"
        weeks.append({
            "week": week_title,
            "week_link": week_link,
            "topics": topics,
            "posts": posts,
            "latest_user": latest_user,
            "latest_time": latest_time
        })
    return weeks

def normalize_text(element):
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() if element else ""


def parse_count(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def truncate_text(value, limit=None):
    limit = max(40, limit or REPLY_EXCERPT_LENGTH)
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def pluralize(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


def short_display_name(name):
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not TELEGRAM_SHORT_NAMES:
        return text

    stop_words = {"BIN", "BINTI", "BT", "B", "A/L", "A/P"}
    parts = [part for part in text.split() if part.upper() not in stop_words]
    return " ".join(parts[:3]) if parts else text


def first_regex(text, pattern, default=""):
    match = re.search(pattern, text or "", re.IGNORECASE)
    return match.group(1).strip() if match else default


def has_class(element, class_name):
    return class_name in (element.get("class") or [])


def class_has_media(value):
    if isinstance(value, list):
        return "media" in value
    return bool(value and "media" in value.split())


def extract_topic_id(url):
    path_parts = [part for part in urlparse(url or "").path.split("/") if part]
    return path_parts[-1] if path_parts else ""


def stable_post_id(topic_id, author, posted_time, content):
    raw = "|".join([topic_id or "", author or "", posted_time or "", content or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def find_main_card(soup):
    cards = soup.select(".content-page .card.m-b-20") or soup.select(".container-fluid .card.m-b-20")
    for card in cards:
        if card.select('a[href*="/forumsv2/rooms/posts/"]') or card.select(".media"):
            return card
    return soup


def find_topic_block(anchor, card):
    node = anchor
    while node and node is not card:
        text = normalize_text(node)
        if "Created By" in text and "Replies" in text:
            return node
        node = node.parent
    return anchor.parent or anchor


def extract_week_topics(soup, course_code, week_title):
    card = find_main_card(soup)
    topics = []
    seen = set()

    for anchor in card.select('a[href*="/forumsv2/rooms/posts/"]'):
        href = anchor.get("href") or ""
        topic_url = urljoin(BASE_URL, href)
        topic_id = extract_topic_id(topic_url)
        title = normalize_text(anchor)

        if not topic_id or topic_id in seen:
            continue

        block_text = normalize_text(find_topic_block(anchor, card))
        title_index = block_text.find(title)
        if title_index >= 0:
            block_text = block_text[title_index:]
        title_pattern = re.escape(title)
        description = first_regex(block_text, rf"{title_pattern}\s+(.*?)\s+Created By\s*:")
        created_by = first_regex(block_text, r"Created By\s*:\s*(.*?)\s+Created At\s*:")
        created_at = first_regex(block_text, r"Created At\s*:\s*(.*?)\s+Replies\s*:")
        replies = parse_count(first_regex(block_text, r"Replies\s*:\s*(\d+)"))
        last_activity = first_regex(block_text, r"Last Activity\s*:\s*(.*?)\s+Open\s*:")
        status = first_regex(block_text, r"Open\s*:\s*([A-Za-z]+)")

        topics.append({
            "course_code": course_code,
            "week": week_title,
            "topic_id": topic_id,
            "topic_title": title or topic_id,
            "topic_url": topic_url,
            "description": truncate_text(description, 240),
            "created_by": created_by,
            "created_at": created_at,
            "replies": replies,
            "last_activity": last_activity,
            "status": status,
        })
        seen.add(topic_id)

    return topics


def direct_media_body(media):
    for child in media.find_all("div", recursive=False):
        if has_class(child, "media-body"):
            return child
    return None


def direct_reply_div(body):
    for child in body.find_all("div", recursive=False):
        if has_class(child, "reply"):
            return child
    return None


def direct_actions(body):
    for child in body.find_all("ul", recursive=False):
        if has_class(child, "actions"):
            return child
    return None


def extract_post_author(body, reply_div):
    author_el = None
    if reply_div:
        author_el = reply_div.find(["h5", "h6", "strong", "b"])
    author_el = author_el or body.find(["h5", "h6", "strong", "b"], recursive=False)
    return normalize_text(author_el)


def extract_posted_time(actions):
    time_el = actions.select_one(".timehover[title]") if actions else None
    if time_el and time_el.get("title"):
        return time_el.get("title").strip()
    actions_text = normalize_text(actions)
    return first_regex(actions_text, r"Posted\s+(.+?)(?:\s+Reply|$)")


def media_label_from_url(url):
    path = urlparse(url or "").path
    filename = os.path.basename(path)
    return filename or "Media"


def extract_media_link(body, reply_div):
    containers = [reply_div, body]
    seen = set()
    media_extensions = (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx",
        ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".mp4", ".mov",
        ".mp3", ".wav",
    )

    for container in containers:
        if not container:
            continue

        for img in container.find_all("img", src=True):
            src = img.get("src", "").strip()
            if not src:
                continue
            media_url = urljoin(BASE_URL, src)
            return media_url, img.get("alt") or media_label_from_url(media_url)

        for anchor in container.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href in seen:
                continue
            seen.add(href)

            href_lower = href.lower()
            classes = anchor.get("class") or []
            text = normalize_text(anchor)
            if (
                "open-DialogReply" in classes
                or href_lower.startswith("#")
                or href_lower.startswith("javascript:")
                or href_lower.startswith("mailto:")
            ):
                continue

            looks_like_media = (
                any(urlparse(href_lower).path.endswith(ext) for ext in media_extensions)
                or "download" in href_lower
                or "attachment" in href_lower
                or "file" in href_lower
                or "media" in href_lower
            )
            if not looks_like_media:
                continue

            media_url = urljoin(BASE_URL, href)
            return media_url, text or media_label_from_url(media_url)

    return "", ""


def extract_topic_posts(soup, topic_id):
    card = find_main_card(soup)
    posts = []
    seen = set()

    def parse_media(media, parent_post_id=None, post_level=0):
        body = direct_media_body(media)
        if not body:
            return

        reply_div = direct_reply_div(body)
        actions = direct_actions(body)
        author = extract_post_author(body, reply_div)
        content = normalize_text(reply_div)
        if author and content.startswith(author):
            content = content[len(author):].strip()
        content = content.replace("read more", "").strip()
        posted_time = extract_posted_time(actions)

        reply_link = actions.select_one('a.open-DialogReply[data-id]') if actions else None
        post_id = reply_link.get("data-id") if reply_link else ""
        if not post_id:
            post_id = stable_post_id(topic_id, author, posted_time, content)
        media_url, media_label = extract_media_link(body, reply_div)

        if post_id not in seen and (content or author or posted_time):
            posts.append({
                "topic_id": topic_id,
                "post_id": post_id,
                "parent_post_id": parent_post_id or "",
                "post_level": post_level,
                "replyable": bool(reply_link),
                "author": author or "Unknown",
                "content_excerpt": truncate_text(content, DETAIL_EXCERPT_LENGTH),
                "media_url": media_url,
                "media_label": media_label,
                "posted_time": posted_time,
            })
            seen.add(post_id)

        for nested in body.find_all("div", class_=class_has_media, recursive=False):
            parse_media(nested, post_id, post_level + 1)

    for media in card.find_all("div", class_=class_has_media):
        if media.find_parent("div", class_=class_has_media):
            continue
        parse_media(media)

    return posts


def ordered_topic_detail_weeks(all_results, priority_changes=None):
    priority_keys = []
    for change in priority_changes or []:
        key = (change.get("course_code"), change.get("week"))
        if key not in priority_keys:
            priority_keys.append(key)

    week_items = []
    for course_code, weeks in all_results.items():
        for week in weeks:
            week_items.append((course_code, week))

    ordered = []
    seen = set()
    for key in priority_keys:
        for course_code, week in week_items:
            item_key = (course_code, week.get("week"))
            if item_key == key and item_key not in seen:
                ordered.append((course_code, week, True))
                seen.add(item_key)

    for course_code, week in week_items:
        item_key = (course_code, week.get("week"))
        if item_key not in seen:
            ordered.append((course_code, week, False))
            seen.add(item_key)

    return ordered


def scrape_topic_details(session, all_results, priority_changes=None):
    if not SCRAPE_TOPIC_DETAILS or TOPIC_DETAIL_LIMIT <= 0:
        return [], []

    topics = []
    posts = []
    seen_topics = set()
    fetched_topics = 0
    priority_count = len(priority_changes or [])
    if priority_count:
        print(f"Prioritizing {priority_count} changed week(s) for topic detail scraping")

    for course_code, week, is_priority in ordered_topic_detail_weeks(all_results, priority_changes):
        if fetched_topics >= TOPIC_DETAIL_LIMIT:
            break
        if parse_count(week.get("topics")) <= 0 and parse_count(week.get("posts")) <= 0:
            continue

        try:
            context_prefix = "changed " if is_priority else ""
            week_response = request_with_retries(
                session,
                "GET",
                week["week_link"],
                context=f"Fetch {context_prefix}topic list for {course_code} {week['week']}",
                timeout=30,
            )
            week_topics = extract_week_topics(safe_soup(week_response.text), course_code, week["week"])
            for topic in week_topics:
                if fetched_topics >= TOPIC_DETAIL_LIMIT:
                    break
                if topic["topic_id"] in seen_topics:
                    continue

                topics.append(topic)
                seen_topics.add(topic["topic_id"])

                topic_response = request_with_retries(
                    session,
                    "GET",
                    topic["topic_url"],
                    context=f"Fetch topic detail for {course_code} {topic['topic_title']}",
                    timeout=30,
                )
                posts.extend(extract_topic_posts(safe_soup(topic_response.text), topic["topic_id"]))
                fetched_topics += 1
                time.sleep(0.5)
        except ScraperError as error:
            print(f"Skipping topic details for {course_code} {week['week']}: {error}")
        time.sleep(0.4)

    print(f"Extracted {len(topics)} topic detail row(s), {len(posts)} post row(s)")
    return topics, posts


def save_to_db(conn, courses, all_results, topic_details=None, post_details=None):
    """Save results to database"""
    c = conn.cursor()
    now = datetime.now()
    
    for course_code, weeks in all_results.items():
        course_data = next((co for co in courses if co['course_code'] == course_code), None)
        if course_data:
            c.execute('''INSERT OR REPLACE INTO courses 
                        (course_code, course_name, forum_url, last_updated) 
                        VALUES (?, ?, ?, ?)''',
                     (course_code, course_data['course_name'], 
                      course_data.get('forum_url', ''), now))
        
        for week in weeks:
            c.execute('''INSERT OR REPLACE INTO weeks
                        (course_code, week_title, week_link, topics, posts, 
                         latest_user, latest_time, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (course_code, week['week'], week['week_link'], 
                      week['topics'], week['posts'], week['latest_user'], 
                      week['latest_time'], now))

    for topic in topic_details or []:
        c.execute('''INSERT OR REPLACE INTO topics
                    (course_code, week_title, topic_id, topic_title, topic_url,
                     description, created_by, created_at, replies, last_activity,
                     status, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (topic['course_code'], topic['week'], topic['topic_id'],
                  topic['topic_title'], topic['topic_url'], topic.get('description', ''),
                  topic.get('created_by', ''), topic.get('created_at', ''),
                  topic.get('replies', 0), topic.get('last_activity', ''),
                  topic.get('status', ''), now))

    for post in post_details or []:
        c.execute('''INSERT OR REPLACE INTO posts
                    (topic_id, post_id, parent_post_id, post_level, replyable, author, content_excerpt,
                     media_url, media_label, posted_time, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (post['topic_id'], post['post_id'], post.get('parent_post_id', ''),
                  post.get('post_level', 0), 1 if post.get('replyable') else 0,
                  post.get('author', ''),
                  post.get('content_excerpt', ''),
                  post.get('media_url', ''), post.get('media_label', ''),
                  post.get('posted_time', ''), now))
    
    conn.commit()

# ============================================================================
# STATE MANAGEMENT
# ============================================================================

def get_current_state(conn):
    """Get current forum state from database"""
    c = conn.cursor()
    
    c.execute('''SELECT c.course_code, c.course_name, w.week_title, w.week_link,
                        w.topics, w.posts, w.latest_user, w.latest_time
                 FROM weeks w
                 JOIN courses c ON w.course_code = c.course_code
                 ORDER BY c.course_code, w.week_title''')
    
    data = []
    for row in c.fetchall():
        data.append({
            'course_code': row[0],
            'course_name': row[1],
            'week': row[2],
            'week_link': row[3],
            'topics': row[4],
            'posts': row[5],
            'latest_user': row[6],
            'latest_time': row[7]
        })
    
    return data


def get_current_topic_state(conn, topic_ids=None):
    c = conn.cursor()
    params = []
    where_clause = ""
    if topic_ids is not None:
        params = list(dict.fromkeys(topic_ids))
        if not params:
            return []
        placeholders = ",".join("?" for _ in params)
        where_clause = f" WHERE topic_id IN ({placeholders})"

    c.execute(f'''SELECT course_code, week_title, topic_id, topic_title, topic_url,
                         description, created_by, created_at, replies, last_activity, status
                  FROM topics
                  {where_clause}
                  ORDER BY course_code, week_title, topic_title''', params)

    data = []
    for row in c.fetchall():
        data.append({
            'course_code': row[0],
            'week': row[1],
            'topic_id': row[2],
            'topic_title': row[3],
            'topic_url': row[4],
            'description': row[5],
            'created_by': row[6],
            'created_at': row[7],
            'replies': row[8],
            'last_activity': row[9],
            'status': row[10],
        })
    return data


def get_current_post_state(conn, topic_ids=None):
    c = conn.cursor()
    params = []
    where_clause = ""
    if topic_ids is not None:
        params = list(dict.fromkeys(topic_ids))
        if not params:
            return []
        placeholders = ",".join("?" for _ in params)
        where_clause = f" WHERE t.topic_id IN ({placeholders})"

    c.execute(f'''SELECT t.course_code, t.week_title, t.topic_id, t.topic_title, t.topic_url,
                         p.post_id, p.parent_post_id, p.author, p.content_excerpt,
                         p.media_url, p.media_label, p.posted_time, p.post_level, p.replyable, p.id
                  FROM posts p
                  JOIN topics t ON p.topic_id = t.topic_id
                  {where_clause}
                  ORDER BY t.course_code, t.week_title, t.topic_title, p.post_level, p.id''', params)

    data = []
    for row in c.fetchall():
        data.append({
            'course_code': row[0],
            'week': row[1],
            'topic_id': row[2],
            'topic_title': row[3],
            'topic_url': row[4],
            'post_id': row[5],
            'parent_post_id': row[6],
            'author': row[7],
            'content_excerpt': row[8],
            'media_url': row[9],
            'media_label': row[10],
            'posted_time': row[11],
            'post_level': row[12] or 0,
            'replyable': bool(row[13]),
        })
    return data


def normalize_snapshot(snapshot):
    if isinstance(snapshot, list):
        return {
            "schema_version": 1,
            "weeks": snapshot,
            "topics": [],
            "posts": [],
        }

    if isinstance(snapshot, dict):
        return {
            "schema_version": snapshot.get("schema_version", 2),
            "weeks": snapshot.get("weeks", []),
            "topics": snapshot.get("topics", []),
            "posts": snapshot.get("posts", []),
        }

    return {
        "schema_version": 2,
        "weeks": [],
        "topics": [],
        "posts": [],
    }


def load_last_snapshot():
    """Load previous state"""
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, 'r') as f:
            return normalize_snapshot(json.load(f))
    return None


def save_current_snapshot(weeks, topics=None, posts=None):
    """Save current state"""
    data = {
        "schema_version": 2,
        "weeks": weeks,
        "topics": topics or [],
        "posts": posts or [],
    }
    with open(SNAPSHOT_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ============================================================================
# CHANGE DETECTION
# ============================================================================

def detect_changes(current, last):
    """Compare states and detect changes"""
    if not last:
        return None
    
    changes = []
    last_dict = {(item['course_code'], item['week']): item for item in last}
    current_dict = {(item['course_code'], item['week']): item for item in current}
    
    for key, curr_item in current_dict.items():
        if key in last_dict:
            last_item = last_dict[key]
            
            try:
                curr_posts = int(curr_item['posts'].split()[0])
                last_posts = int(last_item['posts'].split()[0])
                curr_topics = int(curr_item['topics'].split()[0])
                last_topics = int(last_item['topics'].split()[0])
                
                new_posts = curr_posts - last_posts
                new_topics = curr_topics - last_topics
                
                if new_posts > 0 or new_topics > 0:
                    changes.append({
                        'course_code': curr_item['course_code'],
                        'course_name': curr_item['course_name'],
                        'week': curr_item['week'],
                        'new_posts': new_posts,
                        'new_topics': new_topics,
                        'total_posts': curr_posts,
                        'total_topics': curr_topics,
                        'latest_user': curr_item['latest_user'],
                        'latest_time': curr_item['latest_time'],
                        'week_link': curr_item.get('week_link', '')
                    })
            except (KeyError, TypeError, ValueError) as error:
                print(f"Skipping invalid snapshot row for {key}: {error}")
    
    return changes if changes else None


def group_by(items, key_func):
    grouped = {}
    for item in items:
        grouped.setdefault(key_func(item), []).append(item)
    return grouped


def topic_root_post(topic_posts):
    for post in topic_posts:
        if not post.get("parent_post_id") or post.get("post_level") == 0:
            return post
    return topic_posts[0] if topic_posts else None


def attach_topic_details(changes, current_topics, current_posts, last_snapshot):
    if not changes or not current_topics:
        return changes

    last_topics = last_snapshot.get("topics", []) if last_snapshot else []
    last_posts = last_snapshot.get("posts", []) if last_snapshot else []
    has_detail_history = bool(last_topics or last_posts)
    last_topic_ids = {item.get("topic_id") for item in last_topics}
    last_post_ids_by_topic = group_by(last_posts, lambda item: item.get("topic_id"))
    last_post_ids_by_topic = {
        topic_id: {post.get("post_id") for post in posts}
        for topic_id, posts in last_post_ids_by_topic.items()
    }

    topics_by_week = group_by(current_topics, lambda item: (item.get("course_code"), item.get("week")))
    posts_by_topic = group_by(current_posts, lambda item: item.get("topic_id"))
    topic_limit = max(1, TELEGRAM_MAX_TOPICS_PER_MESSAGE)

    for change in changes:
        week_topics = topics_by_week.get((change["course_code"], change["week"]), [])
        details = []

        for topic in week_topics:
            topic_posts = posts_by_topic.get(topic["topic_id"], [])
            previous_post_ids = last_post_ids_by_topic.get(topic["topic_id"], set())
            new_posts = [
                post for post in topic_posts
                if has_detail_history and post.get("post_id") not in previous_post_ids
            ]
            is_new_topic = has_detail_history and topic["topic_id"] not in last_topic_ids

            if has_detail_history and not is_new_topic and not new_posts:
                continue

            latest_posts = new_posts or topic_posts[-2:]
            root_post = topic_root_post(topic_posts)
            media_post = next((post for post in topic_posts if post.get("media_url")), None)
            details.append({
                "topic_id": topic["topic_id"],
                "topic_title": topic.get("topic_title", ""),
                "topic_url": topic.get("topic_url", ""),
                "description": topic.get("description", ""),
                "created_by": topic.get("created_by", ""),
                "created_at": topic.get("created_at", ""),
                "replies": topic.get("replies", 0),
                "last_activity": topic.get("last_activity", ""),
                "new_topic": is_new_topic,
                "new_posts": len(new_posts),
                "posts": latest_posts[-3:],
                "root_post": root_post,
                "detail_posts": latest_posts[-10:],
                "media_url": media_post.get("media_url", "") if media_post else "",
                "media_label": media_post.get("media_label", "") if media_post else "",
                "exact_post_diff": has_detail_history,
            })

        if not details:
            for topic in week_topics[:topic_limit]:
                topic_posts = posts_by_topic.get(topic["topic_id"], [])
                root_post = topic_root_post(topic_posts)
                media_post = next((post for post in topic_posts if post.get("media_url")), None)
                details.append({
                    "topic_id": topic["topic_id"],
                    "topic_title": topic.get("topic_title", ""),
                    "topic_url": topic.get("topic_url", ""),
                    "description": topic.get("description", ""),
                    "created_by": topic.get("created_by", ""),
                    "created_at": topic.get("created_at", ""),
                    "replies": topic.get("replies", 0),
                    "last_activity": topic.get("last_activity", ""),
                    "new_topic": False,
                    "new_posts": 0,
                    "posts": topic_posts[-2:],
                    "root_post": root_post,
                    "detail_posts": topic_posts[-10:],
                    "media_url": media_post.get("media_url", "") if media_post else "",
                    "media_label": media_post.get("media_label", "") if media_post else "",
                    "exact_post_diff": has_detail_history,
                })

        change["topic_details"] = details[:topic_limit]
        change["topic_details_truncated"] = len(details) > topic_limit

    return changes

# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

def html_text(value, *, quote=False):
    return html.escape(str(value or ""), quote=quote)


def topic_link(topic):
    title = html_text(topic.get("topic_title") or "Open topic")
    url = html_text(topic.get("topic_url"), quote=True)
    if url:
        return f'<a href="{url}">{title}</a>'
    return title


def course_display_name(change):
    course_code = str(change.get("course_code") or "").strip()
    course_name = str(change.get("course_name") or "").strip()
    if course_name.upper().startswith(course_code.upper()):
        course_name = course_name[len(course_code):].strip(" -:–—")
    return course_name or str(change.get("course_name") or "")


def format_count(count, noun, plural=None):
    return f"{count} {pluralize(count, noun, plural)}"


def grouped_posts(posts):
    if not TELEGRAM_DEDUP_REPLIES:
        return [
            {
                "author": post.get("author") or "Unknown",
                "content_excerpt": post.get("content_excerpt") or "",
                "posted_time": post.get("posted_time") or "",
                "count": 1,
            }
            for post in posts
        ]

    grouped = []
    index_by_key = {}
    for post in posts:
        key = (
            post.get("author") or "Unknown",
            post.get("content_excerpt") or "",
        )
        if key in index_by_key:
            item = grouped[index_by_key[key]]
            item["count"] += 1
            if post.get("posted_time"):
                item["posted_time"] = post.get("posted_time")
            continue
        index_by_key[key] = len(grouped)
        grouped.append({
            "author": post.get("author") or "Unknown",
            "content_excerpt": post.get("content_excerpt") or "",
            "posted_time": post.get("posted_time") or "",
            "count": 1,
        })
    return grouped


def topic_button_suffix(index, total):
    return f" {index}" if total > 1 else ""


def callback_buttons_enabled(conn=None):
    return (
        TELEGRAM_INTERACTIVE_BOT
        and conn is not None
        and TELEGRAM_BUTTON_MODE in {"callback", "hybrid"}
    )


def create_telegram_action(conn, action_type, change, topic):
    now = datetime.now()
    expires_at = now + timedelta(hours=max(1, TELEGRAM_ACTION_TTL_HOURS))
    action_id = secrets.token_urlsafe(8)
    payload = {
        "change": {
            "course_code": change.get("course_code"),
            "course_name": change.get("course_name"),
            "week": change.get("week"),
            "latest_user": change.get("latest_user"),
            "latest_time": change.get("latest_time"),
        },
        "topic": topic,
    }
    conn.execute('''INSERT INTO telegram_actions
                   (action_id, action_type, chat_id, course_code, week_title,
                    topic_id, post_id, topic_url, media_url, created_at,
                    expires_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (action_id, action_type, TELEGRAM_CHAT_ID,
                  change.get("course_code", ""), change.get("week", ""),
                  topic.get("topic_id", ""), "", topic.get("topic_url", ""),
                  topic.get("media_url", ""), now.isoformat(),
                  expires_at.isoformat(), json.dumps(payload)))
    return action_id


def build_inline_keyboard(change, conn=None):
    if not TELEGRAM_INLINE_BUTTONS:
        return None

    topic_details = [topic for topic in change.get("topic_details", []) if topic.get("topic_url")]
    if not topic_details:
        if change.get("week_link"):
            return {"inline_keyboard": [[{"text": "Open week", "url": change["week_link"]}]]}
        return None

    total = len(topic_details)
    rows = []
    use_callbacks = callback_buttons_enabled(conn)
    for index, topic in enumerate(topic_details, start=1):
        suffix = topic_button_suffix(index, total)
        if use_callbacks:
            details_id = create_telegram_action(conn, "details", change, topic)
            reply_id = create_telegram_action(conn, "reply", change, topic)
            row = [
                {"text": f"Details{suffix}", "callback_data": f"d:{details_id}"},
                {"text": f"Reply{suffix}", "callback_data": f"r:{reply_id}"},
            ]
        else:
            row = [
                {"text": f"Details{suffix}", "url": topic["topic_url"]},
                {"text": f"Reply{suffix}", "url": topic["topic_url"]},
            ]
        if TELEGRAM_MEDIA_BUTTONS and topic.get("media_url"):
            row.append({"text": f"Media{suffix}", "url": topic["media_url"]})
        row.append({"text": f"Open{suffix}", "url": topic["topic_url"]})
        rows.append(row)

    if use_callbacks:
        conn.commit()

    return {"inline_keyboard": rows}


def build_change_block(change, *, number_topics=False):
    course_name = course_display_name(change)
    lines = [
        f"📚 <b>{html_text(change['course_code'])}</b> — {html_text(course_name)}",
        f"{html_text(change['week'])}",
    ]

    summary = []
    if change['new_topics'] > 0:
        summary.append(f"🆕 {format_count(change['new_topics'], 'new topic')}")
    if change['new_posts'] > 0:
        summary.append(f"💬 {format_count(change['new_posts'], 'new post')}")
    summary.append(f"👤 {html_text(short_display_name(change['latest_user']))}")
    summary.append(f"🕐 {html_text(change['latest_time'])}")
    lines.append(" · ".join(summary))

    topic_details = change.get("topic_details") or []
    for index, topic in enumerate(topic_details, start=1):
        topic_prefix = f"{index}. " if number_topics and len(topic_details) > 1 else ""
        lines.append("")
        lines.append(f"{topic_prefix}🧵 {topic_link(topic)}")
        if topic.get("new_topic"):
            lines.append("🆕 New topic")
        if topic.get("exact_post_diff") and topic.get("new_posts"):
            lines.append(f"💬 {format_count(topic['new_posts'], 'new reply', 'new replies')}")
        elif topic.get("replies") is not None:
            lines.append(f"💬 {format_count(topic.get('replies', 0), 'total reply', 'total replies')}")

        showed_posts = False
        if INCLUDE_REPLY_EXCERPTS:
            for post in grouped_posts(topic.get("posts", [])):
                author = html_text(short_display_name(post.get("author") or "Unknown"))
                posted_time = html_text(post.get("posted_time") or "-")
                excerpt = html_text(truncate_text(post.get("content_excerpt") or "", REPLY_EXCERPT_LENGTH))
                if excerpt:
                    suffix = f" ×{post['count']}" if post.get("count", 1) > 1 else ""
                    lines.append(f"↳ {author}: {excerpt}{suffix}")
                    lines.append(f"  {posted_time}")
                    showed_posts = True

        if not showed_posts and topic.get("last_activity"):
            lines.append(f"🕐 {html_text(topic['last_activity'])}")

    if change.get("topic_details_truncated"):
        lines.append("")
        lines.append("… more changed topics omitted by TELEGRAM_MAX_TOPICS_PER_MESSAGE")

    return "\n".join(lines)


def chunk_telegram_text_blocks(blocks):
    header = "🔔 <b>Forum Updates Detected!</b>"
    footer = f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    max_length = 3900
    messages = []
    current = header

    def close_message(message):
        messages.append(f"{message}\n\n{footer}")

    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(f"{candidate}\n\n{footer}") <= max_length:
            current = candidate
            continue

        if current != header:
            close_message(current)
            current = f"{header}\n\n{block}"
        else:
            current = f"{header}\n\n{block}"

        if len(f"{current}\n\n{footer}") <= max_length:
            continue

        partial = header
        for line in block.splitlines():
            separator = "\n\n" if partial == header else "\n"
            candidate = f"{partial}{separator}{line}"
            if len(f"{candidate}\n\n{footer}") > max_length and partial != header:
                close_message(partial)
                partial = f"{header}\n\n{line}"
            else:
                partial = candidate
        close_message(partial)
        current = header

    if current != header:
        close_message(current)

    return messages


def build_telegram_payloads(changes, conn=None):
    if TELEGRAM_INLINE_BUTTONS:
        payloads = []
        for change in changes:
            block = build_change_block(change, number_topics=True)
            text = "\n\n".join([
                "🔔 <b>Forum Updates Detected!</b>",
                block,
                f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            ])
            keyboard = build_inline_keyboard(change, conn)
            if len(text) <= 3900:
                payload = {"text": text}
                if keyboard:
                    payload["reply_markup"] = keyboard
                payloads.append(payload)
                continue

            chunks = chunk_telegram_text_blocks([block])
            for index, chunk in enumerate(chunks):
                payload = {"text": chunk}
                if index == 0 and keyboard:
                    payload["reply_markup"] = keyboard
                payloads.append(payload)
        return payloads

    return [{"text": message} for message in chunk_telegram_text_blocks(
        [build_change_block(change) for change in changes]
    )]


def build_telegram_messages(changes):
    return [payload["text"] for payload in build_telegram_payloads(changes)]


def send_telegram_notification(changes, conn=None):
    """Send Telegram notification"""
    if not TELEGRAM_ENABLED or not changes:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payloads = build_telegram_payloads(changes, conn)
    sent = 0

    try:
        for payload in payloads:
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": payload["text"],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if payload.get("reply_markup"):
                data["reply_markup"] = json.dumps(payload["reply_markup"])
            response = requests.post(url, data=data, timeout=10)
            if response.status_code != 200:
                print(f"Failed to send Telegram notification: HTTP {response.status_code}")
                return False
            sent += 1
        print(f"Telegram notification sent ({sent} message part(s))")
        return True
    except Exception as e:
        print(f"Telegram notification error: {e}")
        return False

# ============================================================================
# MAIN
# ============================================================================

def run_scrape():
    print(f"\n{'='*80}")
    print(f"UiTM Forum Scraper + Telegram Notifier")
    print(f"Started: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*80}\n")

    verify_dns(urlparse(BASE_URL).hostname or "ufuture.uitm.edu.my")

    conn = init_db()
    s = requests.Session()
    s.headers.update(HEADERS)

    # Login
    try:
        request_with_retries(s, "GET", LOGIN_URL, context="Fetch login page", timeout=30)
        post_data = {
            "data[User][username]": USERNAME,
            "data[User][password]": PASSWORD,
            "data[User][role]": ROLE,
            "_method": "POST"
        }
        request_with_retries(
            s,
            "POST",
            LOGIN_URL,
            context="Submit login",
            data=post_data,
            headers=HEADERS,
            allow_redirects=True,
            timeout=30,
        )
        print("Logged in successfully")
    except ScraperError:
        conn.close()
        raise

    # Get dashboard
    try:
        r_dash = request_with_retries(s, "GET", DASHBOARD_URL, context="Fetch dashboard", timeout=30)
        soup_dash = safe_soup(r_dash.text)
        cards = find_course_cards(soup_dash)
        print(f"Found {len(cards)} course cards")
    except ScraperError:
        conn.close()
        raise

    if not cards:
        conn.close()
        raise ScraperError(
            "No course cards found after login. UiTM may have changed the page layout, "
            "or the login session may not be authenticated."
        )

    courses = []
    for card in cards:
        title, view_url = extract_course_from_card(card)
        if title and view_url:
            code = title.split()[0]
            courses.append({"course_code": code, "course_name": title, "view_url": view_url})

    courses = list({c['course_code']: c for c in courses}.values())
    print(f"Processed {len(courses)} unique courses")

    if not courses:
        conn.close()
        raise ScraperError("No valid course links were extracted from the dashboard.")

    # Scan for forum links
    course_forums = []
    for c in courses:
        try:
            cr = request_with_retries(
                s,
                "GET",
                c["view_url"],
                context=f"Fetch course page for {c['course_code']}",
                timeout=30,
            )
            course_soup = safe_soup(cr.text)
            forum_href = find_forum_link_on_course_page(course_soup)
            if forum_href:
                c["forum_url"] = urljoin(BASE_URL, forum_href)
                course_forums.append(c)
            else:
                print(f"No forum link found for {c['course_code']}")
        except ScraperError as error:
            print(f"Skipping {c['course_code']}: {error}")
        time.sleep(0.6)

    print(f"Found {len(course_forums)} forums")

    if not course_forums:
        conn.close()
        raise ScraperError("No forum links found for any course. Refusing to update an empty snapshot.")

    # Extract weeks
    all_results = {}
    for c in course_forums:
        try:
            fr = request_with_retries(
                s,
                "GET",
                c["forum_url"],
                context=f"Fetch forum for {c['course_code']}",
                timeout=30,
            )
            forum_soup = safe_soup(fr.text)
            weeks = extract_forum_weeks(forum_soup)
            all_results[c["course_code"]] = weeks
            print(f"{c['course_code']}: extracted {len(weeks)} week row(s)")
        except ScraperError as error:
            print(f"Skipping forum for {c['course_code']}: {error}")
        time.sleep(0.4)

    total_weeks = sum(len(w) for w in all_results.values())
    if not all_results or total_weeks == 0:
        conn.close()
        raise ScraperError("No forum week rows extracted. Refusing to update database or snapshot.")

    # Save week overview first so changes can be detected before spending the topic detail limit.
    save_to_db(conn, courses, all_results)

    # Get current week state
    current = get_current_state(conn)

    # Check for week-level changes before topic detail scraping.
    last_snapshot = load_last_snapshot()
    last_weeks = last_snapshot.get("weeks", []) if last_snapshot else None
    changes = detect_changes(current, last_weeks)

    # Extract topic/post details after the week overview has succeeded.
    topic_details, post_details = scrape_topic_details(s, all_results, changes)

    # Save topic details to database.
    save_to_db(conn, courses, all_results, topic_details, post_details)
    print(f"Saved to database: {DB_FILE}")

    fetched_topic_ids = [topic["topic_id"] for topic in topic_details]
    current_topics = get_current_topic_state(conn, fetched_topic_ids)
    current_posts = get_current_post_state(conn, fetched_topic_ids)
    changes = attach_topic_details(changes, current_topics, current_posts, last_snapshot)
    
    # Display results
    print(f"Scrape completed: {len(all_results)} courses, {total_weeks} weeks\n")
    
    # Send notification if changes detected
    if changes:
        print(f"Detected {len(changes)} update(s)!\n")
        for change in changes:
            print(f"{change['course_code']}: {change['new_posts']} new post(s), {change['new_topics']} new topic(s)")
        print()
        send_telegram_notification(changes, conn)
    else:
        if last_snapshot:
            print("No changes detected\n")
        else:
            print("First run - notification system ready\n")
    
    # Save snapshot
    save_current_snapshot(current, current_topics, current_posts)
    
    print(f"Completed: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    conn.close()
    return EXIT_OK


def main():
    try:
        validate_config()
        return run_scrape()
    except ScraperError as error:
        print(f"Scraper failed: {error}")
        return error.exit_code
    except Exception as error:
        print(f"Unexpected scraper failure: {error}")
        return EXIT_FAILURE

if __name__ == "__main__":
    sys.exit(main())
