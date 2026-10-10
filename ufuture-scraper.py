"""
UiTM Forum Scraper + Telegram Notifier (Docker-ready)
Uses environment variables for credentials
"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote, urljoin, urlparse
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
import io
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
TELEGRAM_REPLY_TARGET_LIMIT = int(os.getenv("TELEGRAM_REPLY_TARGET_LIMIT", "8"))
TELEGRAM_BUTTON_MODE = os.getenv("TELEGRAM_BUTTON_MODE", "hybrid").strip().lower()
TELEGRAM_ACTION_TTL_HOURS = int(os.getenv("TELEGRAM_ACTION_TTL_HOURS", "72"))
TELEGRAM_REPLY_DRAFT_TTL_HOURS = int(os.getenv("TELEGRAM_REPLY_DRAFT_TTL_HOURS", "24"))
TELEGRAM_INTERACTIVE_BOT = os.getenv("TELEGRAM_INTERACTIVE_BOT", "true").strip().lower() in {"1", "true", "yes", "on"}
UFUTURE_POST_REPLIES = os.getenv("UFUTURE_POST_REPLIES", "false").strip().lower() in {"1", "true", "yes", "on"}
TOPIC_DETAIL_MODE = os.getenv("TOPIC_DETAIL_MODE", "changed").strip().lower()
TELEGRAM_SILENT_CLASSMATE_REPLIES = os.getenv("TELEGRAM_SILENT_CLASSMATE_REPLIES", "true").strip().lower() in {"1", "true", "yes", "on"}
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_SEND_ATTACHMENTS = os.getenv("TELEGRAM_SEND_ATTACHMENTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ATTACHMENT_MAX_MB = float(os.getenv("ATTACHMENT_MAX_MB", "20"))
ATTACHMENT_MAX_PER_RUN = int(os.getenv("ATTACHMENT_MAX_PER_RUN", "5"))
ATTACHMENT_MAX_ATTEMPTS = 3
DETAIL_BACKFILL_VERSION = "1"
TELEGRAM_LAYOUT = os.getenv("TELEGRAM_LAYOUT", "digest").strip().lower()

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2
EXIT_LOGIN = 3
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


class LoginError(ScraperError):
    exit_code = EXIT_LOGIN


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
    if not TELEGRAM_BOT_TOKEN and not DRY_RUN:
        errors.append("TELEGRAM_BOT_TOKEN not set")
    if not TELEGRAM_CHAT_ID and not DRY_RUN:
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
    ensure_column(c, "posts", "is_own", "INTEGER DEFAULT 0")
    ensure_column(c, "posts", "attachments_json", "TEXT")

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

    # Notifications as sent, so the interactive bot can restore them after
    # editing a message into a topic card.
    c.execute('''CREATE TABLE IF NOT EXISTS telegram_messages (
        chat_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        text TEXT NOT NULL,
        reply_markup_json TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )''')

    # Attachments waiting to be relayed to Telegram. Files are never stored;
    # only the Telegram file_id is kept once sent.
    c.execute('''CREATE TABLE IF NOT EXISTS attachment_queue (
        url TEXT PRIMARY KEY,
        label TEXT,
        course_code TEXT,
        topic_id TEXT,
        topic_title TEXT,
        post_id TEXT,
        reply_to_message_id TEXT,
        silent INTEGER DEFAULT 0,
        status TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        size_bytes INTEGER,
        telegram_file_id TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    
    conn.commit()
    return conn


def ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cursor.fetchall()}
    if column not in columns:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error).lower():
                raise

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
        # UiTM's "Replies" count includes the original post.
        replies = max(0, parse_count(first_regex(block_text, r"Replies\s*:\s*(\d+)")) - 1)
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


def is_avatar_image(img):
    src = (img.get("src") or "").lower()
    return "gambar_warga" in src or "rounded-circle" in (img.get("class") or [])


def extract_attachments(reply_div):
    """Collect attachments from the post's own content only.

    The surrounding media-body also contains nested replies and their avatars,
    so it must not be searched.
    """
    if not reply_div:
        return []

    attachments = []
    seen = set()
    media_extensions = (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx",
        ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".mp4", ".mov",
        ".mp3", ".wav",
    )

    def add(url, label):
        if url and url not in seen:
            seen.add(url)
            attachments.append({"url": url, "label": label or media_label_from_url(url)})

    for anchor in reply_div.select('a[href*="/forumsv2/attachments/download/"]'):
        add(urljoin(BASE_URL, anchor.get("href", "").strip()), normalize_text(anchor))

    for img in reply_div.find_all("img", src=True):
        src = img.get("src", "").strip()
        if src and not src.startswith("data:") and not is_avatar_image(img):
            media_url = urljoin(BASE_URL, src)
            add(media_url, img.get("alt") or media_label_from_url(media_url))

    for anchor in reply_div.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        href_lower = href.lower()
        if (
            not href
            or href_lower.startswith(("#", "javascript:", "mailto:"))
            or "open-DialogReply" in (anchor.get("class") or [])
        ):
            continue
        if any(urlparse(href_lower).path.endswith(ext) for ext in media_extensions):
            add(urljoin(BASE_URL, href), normalize_text(anchor))

    return attachments


def is_own_post(actions):
    # Only the logged-in user's own posts get an Edit action.
    return bool(actions and actions.select_one(".open-DialogEdit"))


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
        for attachment_list in (reply_div.select("ul.attachUL") if reply_div else []):
            # Attachments are listed separately; keep them out of the post text.
            content = content.replace(normalize_text(attachment_list), "").strip()
        posted_time = extract_posted_time(actions)

        reply_link = actions.select_one('a.open-DialogReply[data-id]') if actions else None
        post_id = reply_link.get("data-id") if reply_link else ""
        if not post_id:
            post_id = stable_post_id(topic_id, author, posted_time, content)
        attachments = extract_attachments(reply_div)

        if post_id not in seen and (content or author or posted_time):
            posts.append({
                "topic_id": topic_id,
                "post_id": post_id,
                "parent_post_id": parent_post_id or "",
                "post_level": post_level,
                "replyable": bool(reply_link),
                "is_own": is_own_post(actions),
                "author": author or "Unknown",
                "content_excerpt": truncate_text(content, DETAIL_EXCERPT_LENGTH),
                "media_url": attachments[0]["url"] if attachments else "",
                "media_label": attachments[0]["label"] if attachments else "",
                "attachments": attachments,
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


def scrape_topic_details(session, all_results, priority_changes=None, fetch_all=True):
    if not SCRAPE_TOPIC_DETAILS or TOPIC_DETAIL_LIMIT <= 0:
        return [], []
    if not fetch_all and not priority_changes:
        print("No changed weeks; skipping topic detail scraping")
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
        if not fetch_all and not is_priority:
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
                    (topic_id, post_id, parent_post_id, post_level, replyable, is_own, author,
                     content_excerpt, media_url, media_label, attachments_json, posted_time,
                     last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (post['topic_id'], post['post_id'], post.get('parent_post_id', ''),
                  post.get('post_level', 0), 1 if post.get('replyable') else 0,
                  1 if post.get('is_own') else 0,
                  post.get('author', ''),
                  post.get('content_excerpt', ''),
                  post.get('media_url', ''), post.get('media_label', ''),
                  json.dumps(post.get('attachments') or []),
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
                         p.media_url, p.media_label, p.posted_time, p.post_level, p.replyable, p.id,
                         p.is_own, p.attachments_json
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
            'is_own': bool(row[15]),
            'attachments': parse_attachments_json(row[16]),
        })
    return data


def parse_attachments_json(value):
    try:
        attachments = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return attachments if isinstance(attachments, list) else []


def load_detail_history(conn):
    """Previously stored topic/post ids, in the same shape as a snapshot.

    The posts table keeps every post ever fetched, unlike the snapshot, which
    only holds the topics fetched in the last run.
    """
    c = conn.cursor()
    topics = [{"topic_id": row[0]} for row in c.execute("SELECT DISTINCT topic_id FROM topics")]
    posts = [
        {"topic_id": row[0], "post_id": row[1]}
        for row in c.execute("SELECT topic_id, post_id FROM posts")
    ]
    return {"topics": topics, "posts": posts}


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


def topic_reply_targets(topic_posts, root_post):
    root_post_id = root_post.get("post_id") if root_post else ""
    replyable_posts = [
        post for post in topic_posts
        if post.get("post_id")
        and post.get("post_id") != root_post_id
        and post.get("replyable")
    ]
    target_limit = max(0, TELEGRAM_REPLY_TARGET_LIMIT)
    if not target_limit:
        return []
    return replyable_posts[-target_limit:]


def parse_posted_time(value):
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y %I:%M %p")
    except ValueError:
        return None


def sort_posts_chronologically(posts):
    """UiTM renders posts in thread order, so nested replies are not in time order."""
    indexed = list(enumerate(posts))
    indexed.sort(key=lambda item: (parse_posted_time(item[1].get("posted_time")) or datetime.min, item[0]))
    return [post for _, post in indexed]


def same_person(name, other):
    def normalize(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().upper()
    return bool(normalize(name)) and normalize(name) == normalize(other)


def annotate_topic_posts(topic, topic_posts):
    own_post_ids = {post.get("post_id") for post in topic_posts if post.get("is_own")}
    creator = topic.get("created_by")
    return [
        dict(
            post,
            to_me=bool(post.get("parent_post_id") in own_post_ids and not post.get("is_own")),
            by_creator=same_person(post.get("author"), creator),
        )
        for post in sort_posts_chronologically(topic_posts)
    ]


def build_topic_detail(topic, topic_posts, new_posts, *, is_new_topic, exact_post_diff, display_posts,
                       summary_posts=None):
    root_post = topic_root_post(topic_posts)
    media_post = next((post for post in topic_posts if post.get("media_url")), None)
    important = (
        is_new_topic
        or not exact_post_diff
        or any(post.get("to_me") or post.get("by_creator") for post in new_posts)
    )
    return {
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
        "replied_to_me": sum(1 for post in new_posts if post.get("to_me")),
        "important": important,
        "posts": (summary_posts or display_posts)[-3:],
        "new_post_items": new_posts[-20:],
        "latest_post": next((post for post in reversed(topic_posts) if not post.get("is_own")), None),
        "root_post": root_post,
        "detail_posts": display_posts[-10:],
        "reply_targets": topic_reply_targets(topic_posts, root_post),
        "media_url": media_post.get("media_url", "") if media_post else "",
        "media_label": media_post.get("media_label", "") if media_post else "",
        "exact_post_diff": exact_post_diff,
        # Only attachments from posts known to be new; guessed diffs could resend old files.
        "new_attachments": [
            dict(attachment, post_id=post.get("post_id"))
            for post in new_posts
            for attachment in post.get("attachments") or []
        ] if (exact_post_diff or is_new_topic) else [],
    }


def attach_topic_details(changes, current_topics, current_posts, detail_history):
    if not changes or not current_topics:
        return changes

    last_topics = detail_history.get("topics", []) if detail_history else []
    last_posts = detail_history.get("posts", []) if detail_history else []
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
        own_new_count = 0

        for topic in week_topics:
            topic_posts = annotate_topic_posts(topic, posts_by_topic.get(topic["topic_id"], []))
            previous_post_ids = last_post_ids_by_topic.get(topic["topic_id"], set())
            has_post_history = bool(previous_post_ids)
            is_new_topic = (
                has_detail_history
                and change.get("new_topics", 0) > 0
                and topic["topic_id"] not in last_topic_ids
            )
            if has_detail_history and has_post_history:
                new_posts = [
                    post for post in topic_posts
                    if post.get("post_id") not in previous_post_ids
                ]
            elif is_new_topic:
                new_posts = topic_posts
            else:
                fallback_count = max(1, min(change.get("new_posts", 0), len(topic_posts)))
                new_posts = topic_posts[-fallback_count:] if topic_posts and fallback_count else []
            exact_post_diff = has_detail_history and has_post_history

            own_new_count += sum(1 for post in new_posts if post.get("is_own"))
            new_posts = [post for post in new_posts if not post.get("is_own")]
            if is_new_topic and not new_posts:
                is_new_topic = False

            if has_detail_history and not is_new_topic and not new_posts:
                continue

            others = [post for post in topic_posts if not post.get("is_own")]
            summary_posts = None
            root_post = topic_root_post(topic_posts)
            if is_new_topic and root_post and not root_post.get("is_own"):
                # Lead a new topic with its original post, even if replies already exist.
                replies = [post for post in new_posts if post.get("post_id") != root_post.get("post_id")]
                summary_posts = [root_post] + replies[-2:]
            details.append(build_topic_detail(
                topic, topic_posts, new_posts,
                is_new_topic=is_new_topic,
                exact_post_diff=exact_post_diff,
                display_posts=new_posts or others[-2:],
                summary_posts=summary_posts,
            ))

        if not details and own_new_count:
            # Every new post in this week was written by the logged-in user.
            change["own_only"] = True
            continue

        if not details:
            for topic in week_topics[:topic_limit]:
                topic_posts = annotate_topic_posts(topic, posts_by_topic.get(topic["topic_id"], []))
                others = [post for post in topic_posts if not post.get("is_own")]
                detail = build_topic_detail(
                    topic, topic_posts, [],
                    is_new_topic=False,
                    exact_post_diff=has_detail_history,
                    display_posts=others[-2:],
                )
                detail["important"] = True
                details.append(detail)

        if own_new_count:
            change["new_posts"] = max(0, change.get("new_posts", 0) - own_new_count)
        change["topic_details"] = details[:topic_limit]
        change["topic_details_truncated"] = len(details) > topic_limit
        change["silent"] = bool(
            TELEGRAM_SILENT_CLASSMATE_REPLIES
            and details
            and not any(detail.get("important") for detail in details)
        )

    changes = [change for change in changes if not change.get("own_only")]
    return changes or None

# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

def html_text(value, *, quote=False):
    return html.escape(str(value or ""), quote=quote)


def topic_link(topic):
    return html_text(topic.get("topic_title") or "Open topic")


def plain_telegram_text(text):
    text = re.sub(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre)[^>]*>", "", text)
    text = re.sub(r'<a\s+href="[^"]*"\s*>(.*?)</a>', r"\1", text)
    return html.unescape(text)


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
        return [display_post(post) for post in posts]

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
            item["to_me"] = item["to_me"] or bool(post.get("to_me"))
            continue
        index_by_key[key] = len(grouped)
        grouped.append(display_post(post))
    return grouped


def display_post(post):
    return {
        "author": post.get("author") or "Unknown",
        "content_excerpt": post.get("content_excerpt") or "",
        "posted_time": post.get("posted_time") or "",
        "to_me": bool(post.get("to_me")),
        "by_creator": bool(post.get("by_creator")),
        "attachments": post.get("attachments") or [],
        "count": 1,
    }


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
    replied_to_me = sum(topic.get("replied_to_me", 0) for topic in change.get("topic_details") or [])
    if replied_to_me:
        summary.append(f"↩️ {format_count(replied_to_me, 'reply', 'replies')} to you")
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
                    marker = "📌 " if post.get("by_creator") else ""
                    to_me = " ↩️ you" if post.get("to_me") else ""
                    lines.append(f"↳ {marker}{author}{to_me}: {excerpt}{suffix}")
                    for attachment in post.get("attachments", [])[:3]:
                        lines.append(f"  📎 {html_text(attachment.get('label') or 'Attachment')}")
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
        for change_index, change in enumerate(changes):
            block = build_change_block(change, number_topics=True)
            text = "\n\n".join([
                "🔔 <b>Forum Updates Detected!</b>",
                block,
                f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            ])
            keyboard = build_inline_keyboard(change, conn)
            silent = bool(change.get("silent"))
            if len(text) <= 3900:
                payload = {"text": text, "disable_notification": silent, "change_indexes": [change_index]}
                if keyboard:
                    payload["reply_markup"] = keyboard
                payloads.append(payload)
                continue

            chunks = chunk_telegram_text_blocks([block])
            for index, chunk in enumerate(chunks):
                payload = {"text": chunk, "disable_notification": silent, "change_indexes": [change_index]}
                if index == 0 and keyboard:
                    payload["reply_markup"] = keyboard
                payloads.append(payload)
        return payloads

    silent = all(change.get("silent") for change in changes)
    return [
        {"text": message, "disable_notification": silent, "change_indexes": list(range(len(changes)))}
        for message in chunk_telegram_text_blocks([build_change_block(change) for change in changes])
    ]


def build_telegram_messages(changes):
    return [payload["text"] for payload in build_telegram_payloads(changes)]


# ---------------------------------------------------------------------------
# Digest layout: one message with sound per important topic, plus one silent
# summary of classmates' replies per run.
# ---------------------------------------------------------------------------

NAME_PREFIXES = {
    "MUHAMMAD", "MUHAMAD", "MOHAMAD", "MOHAMMAD", "MOHAMED", "MOHD", "MUHD", "MOHD.",
    "NUR", "NURUL", "NOR", "NOOR", "SITI", "ABDUL", "ABD", "ABD.", "WAN", "NIK", "MEGAT", "TENGKU",
}
NAME_TITLES = {
    "DR": "Dr.", "PROF": "Prof.", "PROFESOR": "Prof.", "PROFESSOR": "Prof.", "MADYA": "Madya",
    "ASSOC": "Assoc.", "PM": "PM", "TS": None, "IR": None,
}
NAME_STOP_WORDS = {"BIN", "BINTI", "BT", "BT.", "B", "B.", "A/L", "A/P"}


def name_case(word):
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))


def short_person(name):
    """'PROFESOR MADYA TS. DR. NORIZAN BINTI ...' -> 'Prof. Madya Dr. Norizan'; 'NUR HASNI MOHAMAD' -> 'Hasni'."""
    words = [word for word in re.sub(r"\s+", " ", str(name or "")).strip().split(" ") if word]
    if not words:
        return "Unknown"
    titles = []
    while words and words[0].upper().rstrip(".") in NAME_TITLES:
        title = NAME_TITLES[words.pop(0).upper().rstrip(".")]
        if title:  # Ts./Ir. are dropped to keep names short
            titles.append(title)
    # The given name is everything before "bin"/"binti".
    stop = next((index for index, word in enumerate(words) if word.upper() in NAME_STOP_WORDS), len(words))
    given = words[:stop] or words
    if titles:
        return " ".join(titles + [name_case(word) for word in given[:2]])
    personal = [word for word in given if word.upper() not in NAME_PREFIXES]
    return name_case((personal or given)[0])


def short_week(week_title):
    match = re.search(r"week\s*(\d+)", str(week_title or ""), re.IGNORECASE)
    return f"W{match.group(1)}" if match else truncate_text(week_title or "", 40)


def clock_time(moment):
    return moment.strftime("%I:%M %p").lstrip("0")


def relative_time(value, now=None):
    moment = parse_posted_time(value)
    if not moment:
        return str(value or "")
    now = now or datetime.now()
    days = (now.date() - moment.date()).days
    if days == 0:
        return f"today {clock_time(moment)}"
    if days == 1:
        return f"yesterday {clock_time(moment)}"
    if 1 < days < 7:
        return f"{moment.strftime('%a')} {clock_time(moment)}"
    return f"{moment.day} {moment.strftime('%b')}"


def absolute_time(value):
    """For stored views (topic card) that may be read days later."""
    moment = parse_posted_time(value)
    if not moment:
        return str(value or "")
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b')}, {clock_time(moment)}"


def featured_posts(topic):
    """Posts worth showing in an important message: original post, creator posts, replies to me."""
    new_posts = topic.get("new_post_items") or []
    featured = []
    if topic.get("new_topic") and topic.get("root_post"):
        featured.append(topic["root_post"])
    featured.extend(
        post for post in new_posts
        if (post.get("by_creator") or post.get("to_me"))
        and post.get("post_id") not in {item.get("post_id") for item in featured}
    )
    if not featured:
        featured = new_posts[-2:] or (topic.get("posts") or [])[-2:]
    return featured[:3]


def post_lines(post, *, excerpt_length=400, when=relative_time):
    marker = "📌 " if post.get("by_creator") else ""
    who = html_text(short_person(post.get("author")))
    action = " replied to you" if post.get("to_me") else ""
    lines = [f"{marker}<b>{who}</b>{action} · {html_text(when(post.get('posted_time')))}"]
    excerpt = truncate_text(post.get("content_excerpt") or "", excerpt_length)
    if excerpt:
        lines.append(html_text(excerpt))
    for attachment in (post.get("attachments") or [])[:5]:
        lines.append(f"📎 {html_text(attachment.get('label') or 'Attachment')}")
    return lines


def topic_heading(change, topic):
    new = "🆕 " if topic.get("new_topic") else ""
    return (
        f"📚 <b>{html_text(change.get('course_code'))}</b> · {html_text(short_week(change.get('week')))}"
        f" · {new}<b>{html_text(topic.get('topic_title') or 'Topic')}</b>"
    )


def build_important_text(change, topic):
    lines = [topic_heading(change, topic)]
    featured = featured_posts(topic)
    for index, post in enumerate(featured):
        if index:
            lines.append("")
        lines.extend(post_lines(post))
    shown = {post.get("post_id") for post in featured}
    others = [post for post in topic.get("new_post_items") or [] if post.get("post_id") not in shown]
    if others:
        names = list(dict.fromkeys(short_person(post.get("author")) for post in others))
        more = f" +{len(names) - 3}" if len(names) > 3 else ""
        lines.append("")
        lines.append(
            f"💬 +{format_count(len(others), 'other new reply', 'other new replies')}"
            f" ({html_text(', '.join(names[:3]))}{more})"
        )
    return "\n".join(lines)[:3900]


def build_topic_card_text(change, topic):
    """Stored with the topic action; uses absolute times since it may be opened days later."""
    lines = [
        f"🧵 <b>{html_text(topic.get('topic_title') or 'Topic')}</b>",
        f"{html_text(change.get('course_code'))} · {html_text(short_week(change.get('week')))}"
        f" · {html_text(course_display_name(change))}",
    ]
    root = topic.get("root_post")
    if root:
        lines.append("")
        lines.extend(post_lines(root, excerpt_length=300, when=absolute_time))
    latest = topic.get("latest_post")
    reply_count = format_count(topic.get("replies") or 0, "reply", "replies")
    lines.append("")
    if latest and (not root or latest.get("post_id") != root.get("post_id")):
        lines.append(
            f"💬 {reply_count} · latest: {html_text(short_person(latest.get('author')))},"
            f" {html_text(absolute_time(latest.get('posted_time')))}"
        )
    else:
        lines.append(f"💬 {reply_count}")
    return "\n".join(lines)[:3900]


def classmate_summary_line(topic):
    posts = [post for post in topic.get("new_post_items") or [] if not post.get("is_own")]
    names = list(dict.fromkeys(short_person(post.get("author")) for post in posts))
    if len(posts) == 1:
        excerpt = truncate_text(posts[0].get("content_excerpt") or "", 80)
        return f"{html_text(names[0])}: \"{html_text(excerpt)}\""
    more = f" +{len(names) - 4}" if len(names) > 4 else ""
    return html_text(", ".join(names[:4])) + more


def build_digest_text(items, total_topics):
    total_posts = sum(topic.get("new_posts") or 0 for _, topic in items)
    lines = [f"💬 <b>Classmate replies</b> · {total_posts}"]
    for index, (change, topic) in enumerate(items, start=1):
        lines.append(
            f"{index}. {html_text(change.get('course_code'))} {html_text(short_week(change.get('week')))}"
            f" · <b>{html_text(topic.get('topic_title') or 'Topic')}</b>:"
            f" {format_count(topic.get('new_posts') or 0, 'new', 'new')}"
        )
        lines.append(f"   {classmate_summary_line(topic)}")
    if total_topics > len(items):
        lines.append(f"…and {total_topics - len(items)} more topic(s)")
    return "\n".join(lines)[:3900]


def topic_button(conn, change, topic, label):
    if not TELEGRAM_INLINE_BUTTONS or not topic.get("topic_url"):
        return None
    if callback_buttons_enabled(conn):
        card_topic = dict(topic, card_text=build_topic_card_text(change, topic))
        action_id = create_telegram_action(conn, "topic", change, card_topic)
        return {"text": label, "callback_data": f"o:{action_id}"}
    return {"text": label, "url": topic["topic_url"]}


def week_fallback_text(change):
    lines = [
        f"📚 <b>{html_text(change.get('course_code'))}</b> · {html_text(short_week(change.get('week')))}",
    ]
    counts = []
    if change.get("new_topics"):
        counts.append(f"🆕 {format_count(change['new_topics'], 'new topic')}")
    if change.get("new_posts"):
        counts.append(f"💬 {format_count(change['new_posts'], 'new post')}")
    counts.append(f"latest: {html_text(short_person(change.get('latest_user')))}, {html_text(change.get('latest_time') or '')}")
    lines.append(" · ".join(counts))
    return "\n".join(lines)


def build_digest_payloads(changes, conn=None):
    important, chatter = [], []
    payloads = []
    for change_index, change in enumerate(changes):
        topics = change.get("topic_details") or []
        if not topics:
            markup = None
            if TELEGRAM_INLINE_BUTTONS and change.get("week_link"):
                markup = {"inline_keyboard": [[{"text": "🌐 Open week", "url": change["week_link"]}]]}
            payloads.append({
                "text": week_fallback_text(change), "reply_markup": markup,
                "disable_notification": False, "change_indexes": [change_index], "targets": [],
            })
            continue
        for topic_index, topic in enumerate(topics):
            item = (change_index, topic_index)
            (important if topic.get("important") else chatter).append(item)

    for change_index, topic_index in important:
        change = changes[change_index]
        topic = change["topic_details"][topic_index]
        button = topic_button(conn, change, topic, f"{truncate_text(topic.get('topic_title') or 'Topic', 40)} ▸")
        payloads.append({
            "text": build_important_text(change, topic),
            "reply_markup": {"inline_keyboard": [[button]]} if button else None,
            "disable_notification": False,
            "change_indexes": [change_index],
            "targets": [(change_index, topic_index)],
            "silent_topics": False,
        })

    if chatter:
        shown = chatter[:max(1, TELEGRAM_MAX_TOPICS_PER_MESSAGE)]
        items = [(changes[ci], changes[ci]["topic_details"][ti]) for ci, ti in shown]
        buttons = [
            topic_button(conn, change, topic, f"{index}. {truncate_text(topic.get('topic_title') or 'Topic', 24)} ▸")
            for index, (change, topic) in enumerate(items, start=1)
        ]
        buttons = [button for button in buttons if button]
        silent = TELEGRAM_SILENT_CLASSMATE_REPLIES
        payloads.append({
            "text": build_digest_text(items, len(chatter)),
            "reply_markup": {"inline_keyboard": [buttons[i:i + 2] for i in range(0, len(buttons), 2)]} if buttons else None,
            "disable_notification": silent,
            "change_indexes": sorted({ci for ci, _ in chatter}),
            "targets": chatter,
            "silent_topics": silent,
        })

    if conn is not None and callback_buttons_enabled(conn):
        conn.commit()
    return payloads


def save_telegram_message(conn, message_id, payload):
    """Remember what a notification looked like so the bot's « Back can restore it."""
    if conn is None or not message_id:
        return
    conn.execute('''INSERT OR REPLACE INTO telegram_messages
                    (chat_id, message_id, text, reply_markup_json, created_at)
                    VALUES (?, ?, ?, ?, ?)''',
                 (str(TELEGRAM_CHAT_ID), str(message_id), payload["text"],
                  json.dumps(payload.get("reply_markup")) if payload.get("reply_markup") else None,
                  datetime.now().isoformat()))
    conn.commit()


def send_telegram_notification(changes, conn=None):
    """Send Telegram notification"""
    if not changes or not (TELEGRAM_ENABLED or DRY_RUN):
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if TELEGRAM_LAYOUT == "classic":
        payloads = build_telegram_payloads(changes, conn)
    else:
        payloads = build_digest_payloads(changes, conn)
    sent = 0

    if DRY_RUN:
        for index, payload in enumerate(payloads, start=1):
            mode = "silent" if payload.get("disable_notification") else "with sound"
            print(f"--- DRY_RUN Telegram message {index}/{len(payloads)} ({mode}) ---")
            print(plain_telegram_text(payload["text"]))
            for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []):
                print("[" + "] [".join(button["text"] for button in row) + "]")
        print(f"DRY_RUN: {len(payloads)} Telegram message(s) not sent")
        return True

    def response_description(response):
        try:
            data = response.json()
        except ValueError:
            return response.text[:200]
        return str(data.get("description") or data)[:300]

    def post_payload(payload, *, include_markup=True, parse_mode="HTML"):
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": payload["text"] if parse_mode else plain_telegram_text(payload["text"]),
            "disable_web_page_preview": True,
            "disable_notification": bool(payload.get("disable_notification")),
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if include_markup and payload.get("reply_markup"):
            data["reply_markup"] = json.dumps(payload["reply_markup"])
        return requests.post(url, data=data, timeout=10)

    try:
        for payload in payloads:
            response = post_payload(payload)
            if response.status_code != 200:
                print(
                    "Telegram send failed with full payload: "
                    f"HTTP {response.status_code} - {response_description(response)}"
                )
                if payload.get("reply_markup"):
                    response = post_payload(payload, include_markup=False)
                    if response.status_code == 200:
                        print("Telegram notification sent without inline buttons after fallback")
                    else:
                        print(
                            "Telegram fallback without buttons failed: "
                            f"HTTP {response.status_code} - {response_description(response)}"
                        )
                if response.status_code != 200:
                    response = post_payload(payload, include_markup=False, parse_mode=None)
                    if response.status_code == 200:
                        print("Telegram notification sent as plain text after fallback")
                    else:
                        print(
                            "Failed to send Telegram notification after fallbacks: "
                            f"HTTP {response.status_code} - {response_description(response)}"
                        )
                        return False
            message_id = telegram_message_id(response)
            for change_index in payload.get("change_indexes", []):
                # Attachments reply to the first message part of their change.
                if message_id and not changes[change_index].get("telegram_message_id"):
                    changes[change_index]["telegram_message_id"] = message_id
            for change_index, topic_index in payload.get("targets", []):
                # In the digest layout, a topic's attachments reply to the message it appeared in.
                topic = changes[change_index]["topic_details"][topic_index]
                topic["telegram_message_id"] = message_id
                topic["silent"] = bool(payload.get("disable_notification"))
            if payload.get("reply_markup"):
                save_telegram_message(conn, message_id, payload)
            sent += 1
        print(f"Telegram notification sent ({sent} message part(s))")
        return True
    except Exception as e:
        print(f"Telegram notification error: {redact_secrets(e)}")
        return False


def telegram_message_id(response):
    try:
        return (response.json().get("result") or {}).get("message_id")
    except (ValueError, AttributeError):
        return None


def redact_secrets(value):
    text = str(value)
    if TELEGRAM_BOT_TOKEN:
        text = text.replace(TELEGRAM_BOT_TOKEN, "<redacted>")
    return text

# ============================================================================
# TELEGRAM ATTACHMENTS
# ============================================================================

class AttachmentTooLarge(Exception):
    def __init__(self, size_bytes):
        super().__init__(f"{size_bytes} bytes")
        self.size_bytes = size_bytes


def format_size(size_bytes):
    if size_bytes < 1024 * 1024:
        return f"{max(1, round(size_bytes / 1024))} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def queue_new_attachments(conn, changes):
    now = datetime.now().isoformat()
    queued = 0
    for change in changes or []:
        for topic in change.get("topic_details") or []:
            for attachment in topic.get("new_attachments") or []:
                cursor = conn.execute('''INSERT OR IGNORE INTO attachment_queue
                                         (url, label, course_code, topic_id, topic_title, post_id,
                                          reply_to_message_id, silent, status, attempts,
                                          created_at, updated_at)
                                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)''',
                                      (attachment["url"], attachment.get("label") or "",
                                       change.get("course_code", ""), topic.get("topic_id", ""),
                                       topic.get("topic_title", ""), attachment.get("post_id") or "",
                                       str(topic.get("telegram_message_id") or change.get("telegram_message_id") or ""),
                                       1 if topic.get("silent", change.get("silent")) else 0, now, now))
                queued += cursor.rowcount
    conn.commit()
    if queued:
        print(f"Queued {queued} new attachment(s) for Telegram")
    return queued


def update_attachment(conn, url, **fields):
    fields["updated_at"] = datetime.now().isoformat()
    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(f"UPDATE attachment_queue SET {assignments} WHERE url = ?", [*fields.values(), url])
    conn.commit()


def open_attachment(session, url):
    response = session.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if urlparse(response.url).path.rstrip("/").endswith("/login") or content_type.startswith("text/html"):
        response.close()
        raise ScraperError("UiTM returned a web page instead of the file (session expired?)")
    return response


def attachment_filename(response, fallback):
    disposition = response.headers.get("Content-Disposition") or ""
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.IGNORECASE)
    name = unquote(match.group(1)).strip() if match else ""
    return os.path.basename(name or fallback or "attachment")


def download_attachment(response, max_bytes):
    """Read the file into memory only; it is never written to disk."""
    length = response.headers.get("Content-Length") or ""
    if length.isdigit() and int(length) > max_bytes:
        raise AttachmentTooLarge(int(length))
    buffer = io.BytesIO()
    for chunk in response.iter_content(64 * 1024):
        buffer.write(chunk)
        if buffer.tell() > max_bytes:
            raise AttachmentTooLarge(buffer.tell())
    buffer.seek(0)
    return buffer


def telegram_api(method, data, files=None, timeout=30):
    return requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        data=data,
        files=files,
        timeout=timeout,
    )


def attachment_reply_fields(row):
    fields = {"chat_id": TELEGRAM_CHAT_ID, "disable_notification": bool(row["silent"])}
    if row["reply_to_message_id"]:
        fields["reply_parameters"] = json.dumps({
            "message_id": int(row["reply_to_message_id"]),
            "allow_sending_without_reply": True,
        })
    return fields


def send_queued_attachments(conn, session):
    if not TELEGRAM_SEND_ATTACHMENTS or not (TELEGRAM_ENABLED or DRY_RUN):
        return 0

    columns = ["url", "label", "course_code", "topic_title", "reply_to_message_id", "silent", "attempts"]
    rows = [
        dict(zip(columns, row))
        for row in conn.execute(f'''SELECT {", ".join(columns)} FROM attachment_queue
                                    WHERE status = 'pending' AND attempts < ?
                                    ORDER BY created_at
                                    LIMIT ?''',
                                (ATTACHMENT_MAX_ATTEMPTS, max(0, ATTACHMENT_MAX_PER_RUN)))
    ]
    max_bytes = int(ATTACHMENT_MAX_MB * 1024 * 1024)
    sent = 0

    for index, row in enumerate(rows):
        if index:
            time.sleep(1)
        label = row["label"] or media_label_from_url(row["url"])
        try:
            response = open_attachment(session, row["url"])
            try:
                filename = attachment_filename(response, label)
                if DRY_RUN:
                    length = response.headers.get("Content-Length") or ""
                    size = format_size(int(length)) if length.isdigit() else "unknown size"
                    action = "send a link (over limit)" if length.isdigit() and int(length) > max_bytes else "send file"
                    print(f"DRY_RUN: would {action}: {filename} ({size})")
                    continue
                buffer = download_attachment(response, max_bytes)
            finally:
                response.close()

            caption = truncate_text(f"📎 {label}\n{row['course_code']} · {row['topic_title']}", 1000)
            result = telegram_api(
                "sendDocument",
                {**attachment_reply_fields(row), "caption": caption},
                files={"document": (filename, buffer)},
                timeout=120,
            )
            if result.status_code != 200:
                raise ScraperError(f"Telegram sendDocument failed: HTTP {result.status_code}")
            document = (result.json().get("result") or {}).get("document") or {}
            update_attachment(
                conn, row["url"],
                status="sent", attempts=row["attempts"] + 1,
                size_bytes=buffer.getbuffer().nbytes,
                telegram_file_id=document.get("file_id"), last_error=None,
            )
            sent += 1
            print(f"Sent attachment to Telegram: {filename} ({format_size(buffer.getbuffer().nbytes)})")
        except AttachmentTooLarge as error:
            text = (
                f"📎 {label} is too large to send here "
                f"({format_size(error.size_bytes)}, limit {ATTACHMENT_MAX_MB:g} MB). Open it in UiTM."
            )
            markup = json.dumps({"inline_keyboard": [[{"text": "Open in UiTM", "url": row["url"]}]]})
            try:
                telegram_api("sendMessage", {**attachment_reply_fields(row), "text": text, "reply_markup": markup})
            except RequestException as send_error:
                print(f"Too-large notice failed: {redact_secrets(send_error)}")
            update_attachment(
                conn, row["url"],
                status="too_large", attempts=row["attempts"] + 1,
                size_bytes=error.size_bytes, last_error=None,
            )
            print(f"Attachment too large, sent link instead: {label} ({format_size(error.size_bytes)})")
        except (RequestException, ScraperError, ValueError) as error:
            attempts = row["attempts"] + 1
            message = redact_secrets(error)[:300]
            update_attachment(
                conn, row["url"],
                status="failed" if attempts >= ATTACHMENT_MAX_ATTEMPTS else "pending",
                attempts=attempts, last_error=message,
            )
            print(f"Attachment {label} failed (attempt {attempts}/{ATTACHMENT_MAX_ATTEMPTS}): {message}")

    return sent

# ============================================================================
# MAIN
# ============================================================================

def login(session):
    """Log the session in to UiTM. Shared with the interactive bot for file downloads."""
    request_with_retries(session, "GET", LOGIN_URL, context="Fetch login page", timeout=30)
    post_data = {
        "data[User][username]": USERNAME,
        "data[User][password]": PASSWORD,
        "data[User][role]": ROLE,
        "_method": "POST"
    }
    login_response = request_with_retries(
        session,
        "POST",
        LOGIN_URL,
        context="Submit login",
        data=post_data,
        headers=HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    # A successful login redirects away from /login (to /courses/list_course).
    if urlparse(login_response.url).path.rstrip("/").endswith("/login"):
        raise LoginError(
            "UiTM login was rejected (still on the login page). "
            "Check UFUTURE_USERNAME/UFUTURE_PASSWORD; not retrying to avoid account lockout."
        )


def get_bot_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM telegram_bot_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def muted_courses(conn):
    try:
        return set(json.loads(get_bot_state(conn, "muted_courses", "[]")))
    except (TypeError, ValueError):
        return set()


def apply_course_mutes(conn, changes):
    """Muted courses (set with /mute in Telegram) only notify about replies to your posts."""
    muted = muted_courses(conn)
    if not changes or not muted:
        return changes
    kept = []
    for change in changes:
        if change.get("course_code") not in muted:
            kept.append(change)
            continue
        topics = [topic for topic in change.get("topic_details") or [] if topic.get("replied_to_me")]
        if topics:
            kept.append(dict(change, topic_details=topics, silent=False))
        else:
            print(f"{change.get('course_code')} is muted; skipping {change.get('week')}")
    return kept or None


def prune_old_courses(conn, current_codes):
    """Delete courses that are no longer on the dashboard (e.g. last semester) and their data.

    current_codes comes from the dashboard course cards, not from forums that loaded,
    so a course whose page failed to load this run is kept.
    """
    current_codes = {code for code in current_codes if code}
    if not current_codes:
        return []
    stored = set()
    for table in ("courses", "weeks", "topics"):
        stored |= {row[0] for row in conn.execute(f"SELECT DISTINCT course_code FROM {table}")}
    old_codes = sorted(code for code in stored - current_codes if code)
    if not old_codes:
        return []

    marks = ",".join("?" for _ in old_codes)
    conn.execute(
        f"DELETE FROM posts WHERE topic_id IN (SELECT topic_id FROM topics WHERE course_code IN ({marks}))",
        old_codes,
    )
    for table in ("topics", "weeks", "courses", "attachment_queue", "telegram_actions"):
        conn.execute(f"DELETE FROM {table} WHERE course_code IN ({marks})", old_codes)
    muted = muted_courses(conn)
    if muted & set(old_codes):
        conn.execute(
            "INSERT OR REPLACE INTO telegram_bot_state (key, value) VALUES ('muted_courses', ?)",
            (json.dumps(sorted(muted - set(old_codes))),),
        )
    conn.commit()
    print(f"Removed {len(old_codes)} old course(s) no longer on the dashboard: {', '.join(old_codes)}")
    return old_codes


def record_run_status(ok, reason=""):
    """Last success/failure for the bot's /status command; never fails the run."""
    stamp = datetime.now().strftime("%d/%m/%Y %I:%M %p")
    values = {"last_success_at": stamp} if ok else {"last_failure_at": stamp, "last_failure_reason": reason[:200]}
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30)
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_bot_state (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT OR REPLACE INTO telegram_bot_state (key, value) VALUES (?, ?)", values.items())
        conn.commit()
        conn.close()
    except sqlite3.Error as error:
        print(f"Could not record run status: {error}")


def failure_summary(error):
    if isinstance(error, LoginError):
        return "UiTM login rejected"
    if isinstance(error, TemporaryNetworkError):
        return "network or DNS problem"
    if isinstance(error, ConfigError):
        return "missing configuration"
    return truncate_text(redact_secrets(error), 160)


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
        login(s)
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

    # Topic/post ids seen in earlier runs; read before this run's details are saved.
    detail_history = load_detail_history(conn)
    # One full pass refills fields older versions never stored (attachments, own posts).
    needs_backfill = get_bot_state(conn, "detail_backfill_version") != DETAIL_BACKFILL_VERSION
    fetch_all_topics = TOPIC_DETAIL_MODE == "all" or not detail_history["topics"] or needs_backfill
    if needs_backfill and detail_history["topics"]:
        print("Refreshing all topic details once to fill in attachments and own posts")

    # Extract topic/post details after the week overview has succeeded.
    topic_details, post_details = scrape_topic_details(s, all_results, changes, fetch_all_topics)
    if needs_backfill and topic_details:
        conn.execute(
            "INSERT OR REPLACE INTO telegram_bot_state (key, value) VALUES ('detail_backfill_version', ?)",
            (DETAIL_BACKFILL_VERSION,),
        )
        conn.commit()

    # Save topic details to database.
    save_to_db(conn, courses, all_results, topic_details, post_details)
    print(f"Saved to database: {DB_FILE}")

    fetched_topic_ids = [topic["topic_id"] for topic in topic_details]
    current_topics = get_current_topic_state(conn, fetched_topic_ids)
    current_posts = get_current_post_state(conn, fetched_topic_ids)
    changes = attach_topic_details(changes, current_topics, current_posts, detail_history)
    changes = apply_course_mutes(conn, changes)
    
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

    # Relay attachments from new posts, plus any still pending from earlier runs.
    if TELEGRAM_SEND_ATTACHMENTS:
        try:
            queue_new_attachments(conn, changes)
            send_queued_attachments(conn, s)
        except Exception as error:
            print(f"Attachment relay error: {redact_secrets(error)}")
    
    # Save snapshot
    save_current_snapshot(current, current_topics, current_posts)

    # Only after a fully successful scrape: drop courses that left the dashboard.
    prune_old_courses(conn, [course["course_code"] for course in courses])
    
    print(f"Completed: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    conn.close()
    return EXIT_OK


def main():
    try:
        validate_config()
        code = run_scrape()
        record_run_status(True)
        return code
    except ScraperError as error:
        print(f"Scraper failed: {error}")
        record_run_status(False, failure_summary(error))
        return error.exit_code
    except Exception as error:
        print(f"Unexpected scraper failure: {redact_secrets(error)}")
        record_run_status(False, f"unexpected error ({type(error).__name__})")
        return EXIT_FAILURE

if __name__ == "__main__":
    sys.exit(main())
