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
from datetime import datetime
import json
import os
import sys
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
    
    conn.commit()
    return conn

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

def save_to_db(conn, courses, all_results):
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
    
    conn.commit()

# ============================================================================
# STATE MANAGEMENT
# ============================================================================

def get_current_state(conn):
    """Get current forum state from database"""
    c = conn.cursor()
    
    c.execute('''SELECT c.course_code, c.course_name, w.week_title, w.topics, w.posts, w.latest_user, w.latest_time
                 FROM weeks w
                 JOIN courses c ON w.course_code = c.course_code
                 ORDER BY c.course_code, w.week_title''')
    
    data = []
    for row in c.fetchall():
        data.append({
            'course_code': row[0],
            'course_name': row[1],
            'week': row[2],
            'topics': row[3],
            'posts': row[4],
            'latest_user': row[5],
            'latest_time': row[6]
        })
    
    return data

def load_last_snapshot():
    """Load previous state"""
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, 'r') as f:
            return json.load(f)
    return None

def save_current_snapshot(data):
    """Save current state"""
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
                        'latest_time': curr_item['latest_time']
                    })
            except (KeyError, TypeError, ValueError) as error:
                print(f"Skipping invalid snapshot row for {key}: {error}")
    
    return changes if changes else None

# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

def send_telegram_notification(changes):
    """Send Telegram notification"""
    if not TELEGRAM_ENABLED or not changes:
        return False
    
    message = "🔔 <b>Forum Updates Detected!</b>\n\n"
    
    for change in changes:
        message += f"📚 <b>{change['course_code']}</b>\n"
        message += f"   {change['course_name']}\n"
        message += f"   Week: {change['week']}\n"
        
        if change['new_topics'] > 0:
            message += f"   🆕 {change['new_topics']} new topic(s)\n"
        if change['new_posts'] > 0:
            message += f"   💬 {change['new_posts']} new post(s)\n"
        
        message += f"   👤 {change['latest_user']}\n"
        message += f"   🕐 {change['latest_time']}\n\n"
    
    message += f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print("Telegram notification sent")
            return True
        else:
            print(f"Failed to send: {response.text}")
            return False
    except Exception as e:
        print(f"Error: {e}")
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

    # Save to database
    save_to_db(conn, courses, all_results)
    print(f"Saved to database: {DB_FILE}")
    
    # Get current state
    current = get_current_state(conn)
    
    # Check for changes
    last = load_last_snapshot()
    changes = detect_changes(current, last)
    
    # Display results
    print(f"Scrape completed: {len(all_results)} courses, {total_weeks} weeks\n")
    
    # Send notification if changes detected
    if changes:
        print(f"Detected {len(changes)} update(s)!\n")
        for change in changes:
            print(f"{change['course_code']}: {change['new_posts']} new post(s), {change['new_topics']} new topic(s)")
        print()
        send_telegram_notification(changes)
    else:
        if last:
            print("No changes detected\n")
        else:
            print("First run - notification system ready\n")
    
    # Save snapshot
    save_current_snapshot(current)
    
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
