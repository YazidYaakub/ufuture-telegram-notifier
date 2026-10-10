import html
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import requests


DEFAULT_DB_FILE = os.path.join("/app", "data", "ufuture_forums.db")
DB_FILE = os.getenv("DB_PATH", DEFAULT_DB_FILE)
TG_TOKEN = os.getenv("TELEGRAM_" + "BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_" + "CHAT_ID")
INTERACTIVE_ENABLED = os.getenv("TELEGRAM_INTERACTIVE_BOT", "true").strip().lower() in {"1", "true", "yes", "on"}
REPLY_DRAFT_TTL_HOURS = int(os.getenv("TELEGRAM_REPLY_DRAFT_TTL_HOURS", "24"))
POST_REPLIES_ENABLED = (
    os.getenv("UFUTURE_REPLY_ENABLED", os.getenv("UFUTURE_POST_REPLIES", "false"))
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)
REPLY_CONFIRM_BEFORE_SEND = os.getenv("REPLY_CONFIRM_BEFORE_SEND", "true").strip().lower() in {"1", "true", "yes", "on"}
REPLY_POST_TIMEOUT_SECONDS = int(os.getenv("REPLY_POST_TIMEOUT_SECONDS", "45"))
REPLY_MIN_INTERVAL_SECONDS = int(os.getenv("REPLY_MIN_INTERVAL_SECONDS", "60"))
REPLY_MAX_FILES = 5
TELEGRAM_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024  # Bot API getFile limit
TELEGRAM_ALLOWED_USER_IDS_PLACEHOLDERS = {"your_telegram_user_id", "telegram_user_id", "none", "null"}
TELEGRAM_ALLOWED_USER_IDS = {
    item
    for item in (value.strip() for value in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(","))
    if item and item.lower() not in TELEGRAM_ALLOWED_USER_IDS_PLACEHOLDERS
}
MESSAGE_RETENTION_DAYS = 30
DETAILS_MAX_LENGTH = 3900
DROP_PENDING_UPDATES = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "true").strip().lower() in {"1", "true", "yes", "on"}
API_BASE = ("https://api.telegram.org/" + "bot" + TG_TOKEN) if TG_TOKEN else ""
RUNNING = True


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def html_text(value, *, quote=False):
    return html.escape(str(value or ""), quote=quote)


def connect_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_actions (
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
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_pending_replies (
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
    ensure_column(conn, "telegram_pending_replies", "topic_url", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "source_action_id", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "target_post_id", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "target_author", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "target_excerpt", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "target_posted_time", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "target_label", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "submitted_at", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "result_message", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "dedupe_key", "TEXT")
    ensure_column(conn, "telegram_pending_replies", "attachments_json", "TEXT")
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_bot_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_messages (
        chat_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        text TEXT NOT NULL,
        reply_markup_json TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )''')
    conn.commit()


def ensure_column(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as error:
            if "duplicate column name" not in str(error).lower():
                raise


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM telegram_bot_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO telegram_bot_state (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()


def cleanup_expired(conn):
    now = datetime.now().isoformat()
    conn.execute("DELETE FROM telegram_actions WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM telegram_pending_replies WHERE expires_at < ?", (now,))
    stored_cutoff = (datetime.now() - timedelta(days=MESSAGE_RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM telegram_messages WHERE created_at < ?", (stored_cutoff,))
    conn.commit()


def is_allowed_chat(chat_id):
    return TG_CHAT_ID and str(chat_id) == str(TG_CHAT_ID)


def is_allowed_user(user_id):
    return not TELEGRAM_ALLOWED_USER_IDS or str(user_id) in TELEGRAM_ALLOWED_USER_IDS


def telegram_post(method, data):
    response = requests.post(f"{API_BASE}/{method}", data=data, timeout=15)
    if response.status_code != 200:
        print(f"Telegram {method} failed: HTTP {response.status_code}")
        return None
    return response.json()


def send_message(chat_id, text, reply_markup=None, reply_to_message_id=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    return telegram_post("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    return telegram_post("editMessageText", data)


def answer_callback(callback_id, text=None):
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    telegram_post("answerCallbackQuery", data)


def load_action(conn, action_id, expected_type=None):
    row = conn.execute("SELECT * FROM telegram_actions WHERE action_id = ?", (action_id,)).fetchone()
    if not row:
        return None, "Action expired or not found."
    expected_types = (expected_type,) if isinstance(expected_type, str) else expected_type
    if expected_types and row["action_type"] not in expected_types:
        return None, "This button no longer matches the expected action."
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None, "Action expiry is invalid."
    if datetime.now() > expires_at:
        return None, "Action expired."
    conn.execute(
        "UPDATE telegram_actions SET used_at = ? WHERE action_id = ?",
        (datetime.now().isoformat(), action_id),
    )
    conn.commit()
    return row, None


def action_payload(row):
    try:
        return json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def truncate_text(value, max_length=180):
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def course_display(change):
    course_name = str(change.get("course_name") or "")
    course_code = str(change.get("course_code") or "")
    if course_code and course_name.upper().startswith(course_code.upper()):
        course_name = course_name[len(course_code):].strip(" -:") or course_name
    return course_code, course_name


def replyable_post(post):
    return bool(post.get("post_id")) and bool(post.get("replyable", True))


def target_author_label(author):
    return truncate_text(author or "Unknown", 24)


def reply_targets(row):
    payload = action_payload(row)
    topic = payload.get("topic", {})
    root_post = topic.get("root_post") or {}
    posts = topic.get("reply_targets") or topic.get("detail_posts") or topic.get("posts", []) or []
    targets = []
    if replyable_post(root_post):
        targets.append({
            "key": "root",
            "label": "Original post",
            "post": root_post,
        })

    root_post_id = root_post.get("post_id") if root_post else None
    reply_index = 1
    seen = {root_post_id} if root_post_id else set()
    for post in posts:
        post_id = post.get("post_id")
        if not post_id or post_id in seen:
            continue
        seen.add(post_id)
        if not replyable_post(post):
            continue
        targets.append({
            "key": f"reply{reply_index}",
            "label": f"Reply {reply_index}: {target_author_label(post.get('author'))}",
            "post": post,
        })
        reply_index += 1
        if reply_index > 8:
            break
    return targets


def find_reply_target(row, target_key):
    for target in reply_targets(row):
        if target["key"] == target_key:
            return target
    return None


def build_target_picker_text(row):
    payload = action_payload(row)
    topic = payload.get("topic", {})
    lines = [
        f"Choose reply target for <b>{html_text(topic.get('topic_title') or 'Forum topic')}</b>:",
        "",
    ]
    targets = reply_targets(row)
    for target in targets:
        post = target["post"]
        lines.append(f"<b>{html_text(target['label'])}</b>")
        lines.append(html_text(post.get("author") or "Unknown"))
        if post.get("content_excerpt"):
            lines.append(html_text(truncate_text(post.get("content_excerpt"), 160)))
        if post.get("posted_time"):
            lines.append(html_text(post.get("posted_time")))
        lines.append("")
    lines.append("Pick one target, then type your reply.")
    return "\n".join(lines).strip()


def build_target_keyboard(action_id, targets):
    rows = []
    for target in targets:
        rows.append([{"text": target["label"], "callback_data": f"t:{action_id}:{target['key']}"}])
    rows.append([{"text": "Cancel", "callback_data": "x:reply"}])
    return {"inline_keyboard": rows}


def build_details_text(row):
    payload = action_payload(row)
    change = payload.get("change", {})
    topic = payload.get("topic", {})
    root_post = topic.get("root_post") or {}
    posts = topic.get("detail_posts") or topic.get("posts", []) or []
    course_code, course_name = course_display(change)
    lines = [
        f"🧵 <b>{html_text(topic.get('topic_title') or 'Forum topic')}</b>",
        "",
        f"Course: {html_text(course_code)} - {html_text(course_name)}",
        f"Week: {html_text(change.get('week'))}",
    ]
    if topic.get("replies") is not None:
        lines.append(f"Total replies: {html_text(topic.get('replies'))}")
    if topic.get("last_activity"):
        lines.append(f"Last activity: {html_text(topic.get('last_activity'))}")

    if root_post:
        lines.extend(["", "<b>Original post</b>"])
        lines.append(html_text(root_post.get("author") or "Unknown"))
        if root_post.get("content_excerpt"):
            lines.append(html_text(root_post.get("content_excerpt")))
        if root_post.get("posted_time"):
            lines.append(html_text(root_post.get("posted_time")))

    root_post_id = root_post.get("post_id") if root_post else None
    thread = topic.get("thread_posts")
    reply_posts = thread if thread is not None else [post for post in posts if post.get("post_id") != root_post_id]
    if reply_posts:
        lines.extend(["", "<b>Latest replies</b>" if thread is not None else "<b>New/latest replies</b>"])
        for index, post in enumerate(reply_posts[:10], start=1):
            author = "🙋 You" if post.get("is_own") else html_text(post.get("author") or "Unknown")
            lines.append(f"{index}. {author}")
            if post.get("content_excerpt"):
                lines.append(f"   {html_text(post.get('content_excerpt'))}")
            if post.get("posted_time"):
                lines.append(f"   {html_text(post.get('posted_time'))}")
    elif not root_post:
        lines.extend(["", "No stored reply excerpt is available for this topic."])
    if row["topic_url"]:
        lines.extend(["", f"Open topic: {html_text(row['topic_url'])}"])
    return clip_html_lines(lines, DETAILS_MAX_LENGTH)


def clip_html_lines(lines, max_length):
    """Drop whole lines (never cut inside a tag) to fit Telegram's message limit."""
    text = "\n".join(lines)
    if len(text) <= max_length:
        return text
    kept = []
    for line in lines:
        if len("\n".join(kept + [line, "…"])) > max_length:
            break
        kept.append(line)
    return "\n".join(kept + ["…"])


def start_reply_draft(conn, row, chat_id, user_id, action_id, target):
    payload = action_payload(row)
    topic = payload.get("topic", {})
    post = target["post"]
    now = datetime.now()
    pending_id = secrets.token_urlsafe(8)
    expires_at = now + timedelta(hours=max(1, REPLY_DRAFT_TTL_HOURS))
    conn.execute('''INSERT INTO telegram_pending_replies
                   (id, chat_id, user_id, topic_id, post_id, topic_url,
                    source_action_id, target_post_id, target_author, target_excerpt,
                    target_posted_time, target_label, topic_title, draft_text, state,
                    created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (pending_id, str(chat_id), str(user_id), row["topic_id"] or "",
                  row["post_id"] or "", row["topic_url"] or "", action_id,
                  post.get("post_id") or "", post.get("author") or "Unknown",
                  truncate_text(post.get("content_excerpt"), 500),
                  post.get("posted_time") or "", target["label"],
                  topic.get("topic_title") or "Forum topic", "", "awaiting_text",
                  now.isoformat(), now.isoformat(), expires_at.isoformat()))
    conn.commit()
    return pending_id, topic.get("topic_title") or "Forum topic"


def latest_pending_reply(conn, chat_id, user_id):
    return conn.execute('''SELECT * FROM telegram_pending_replies
                           WHERE chat_id = ? AND user_id = ?
                             AND state = 'awaiting_text'
                             AND expires_at >= ?
                           ORDER BY updated_at DESC
                           LIMIT 1''',
                        (str(chat_id), str(user_id), datetime.now().isoformat())).fetchone()


def latest_open_reply(conn, chat_id, user_id):
    return conn.execute('''SELECT * FROM telegram_pending_replies
                           WHERE chat_id = ? AND user_id = ?
                             AND state IN ('awaiting_text', 'preview')
                             AND expires_at >= ?
                           ORDER BY updated_at DESC
                           LIMIT 1''',
                        (str(chat_id), str(user_id), datetime.now().isoformat())).fetchone()


def update_pending_state(conn, pending_id, state, draft_text=None):
    conn.execute('''UPDATE telegram_pending_replies
                    SET state = ?, draft_text = COALESCE(?, draft_text), updated_at = ?
                    WHERE id = ?''',
                 (state, draft_text, datetime.now().isoformat(), pending_id))
    conn.commit()


def update_pending_preview(conn, pending_id, pending, draft_text, attachments=None):
    if attachments is None:
        attachments = draft_attachments(pending)
    dedupe_source = "|".join([
        pending["topic_id"] or "",
        pending["target_post_id"] or "",
        draft_text or "",
        ",".join(sorted(item.get("file_unique_id") or item.get("file_id") or "" for item in attachments)),
    ])
    dedupe_key = hashlib.sha256(dedupe_source.encode("utf-8")).hexdigest()
    conn.execute('''UPDATE telegram_pending_replies
                    SET state = 'preview', draft_text = ?, attachments_json = ?, dedupe_key = ?, updated_at = ?
                    WHERE id = ?''',
                 (draft_text, json.dumps(attachments), dedupe_key, datetime.now().isoformat(), pending_id))
    conn.commit()


def update_pending_result(conn, pending_id, state, result_message):
    conn.execute('''UPDATE telegram_pending_replies
                    SET state = ?, result_message = ?, updated_at = ?
                    WHERE id = ?''',
                 (state, result_message, datetime.now().isoformat(), pending_id))
    conn.commit()


def load_pending(conn, pending_id):
    row = conn.execute("SELECT * FROM telegram_pending_replies WHERE id = ?", (pending_id,)).fetchone()
    if not row:
        return None
    if datetime.now() > datetime.fromisoformat(row["expires_at"]):
        return None
    return row


def handle_details(conn, callback, action_id):
    row, error = load_action(conn, action_id, "details")
    if error:
        answer_callback(callback["id"], error)
        return
    answer_callback(callback["id"], "Details opened.")
    message = callback.get("message") or {}
    send_message(message["chat"]["id"], build_details_text(row), reply_to_message_id=message.get("message_id"))


def topic_card_keyboard(action_id, topic_url, *, back=None, has_files=False):
    rows = [[
        {"text": "📄 Details", "callback_data": f"cd:{action_id}"},
        {"text": "✍️ Reply", "callback_data": f"r:{action_id}"},
    ]]
    if has_files:
        rows.append([{"text": "📎 Files", "callback_data": f"tf:{action_id}"}])
    if topic_url:
        rows.append([{"text": "🌐 Open in UiTM", "url": topic_url}])
    # Cards opened from /latest or /courses go back to that list; from a notification, to the notification.
    rows.append([{"text": "« Back", "callback_data": back or f"bm:{action_id}"}])
    return {"inline_keyboard": rows}


def handle_open_card(conn, callback, action_id):
    row, error = load_action(conn, action_id, "topic")
    if error:
        answer_callback(callback["id"], error)
        return
    message = callback.get("message") or {}
    topic = action_payload(row).get("topic", {})
    text = topic.get("card_text") or f"🧵 <b>{html_text(topic.get('topic_title') or 'Forum topic')}</b>"
    try:
        has_files = bool(attachment_rows(conn, "p.topic_id = ?", (row["topic_id"],)))
    except Exception as error:
        print(f"Topic file lookup failed: {type(error).__name__}")
        has_files = False
    answer_callback(callback["id"])
    edit_message(
        message["chat"]["id"], message["message_id"], text,
        topic_card_keyboard(action_id, row["topic_url"], back=topic.get("back"), has_files=has_files),
    )


def handle_card_details(conn, callback, action_id):
    row, error = load_action(conn, action_id, "topic")
    if error:
        answer_callback(callback["id"], error)
        return
    message = callback.get("message") or {}
    rows = [[
        {"text": "« Back", "callback_data": f"o:{action_id}"},
        {"text": "✍️ Reply", "callback_data": f"r:{action_id}"},
    ]]
    if row["topic_url"]:
        rows.append([{"text": "🌐 Open in UiTM", "url": row["topic_url"]}])
    answer_callback(callback["id"])
    edit_message(message["chat"]["id"], message["message_id"], build_details_text(row), {"inline_keyboard": rows})


def handle_back_to_message(conn, callback):
    message = callback.get("message") or {}
    chat_id = message["chat"]["id"]
    stored = conn.execute(
        "SELECT text, reply_markup_json FROM telegram_messages WHERE chat_id = ? AND message_id = ?",
        (str(chat_id), str(message.get("message_id"))),
    ).fetchone()
    if not stored:
        answer_callback(callback["id"], "Original message is no longer available.")
        return
    answer_callback(callback["id"])
    markup = json.loads(stored["reply_markup_json"]) if stored["reply_markup_json"] else None
    edit_message(chat_id, message["message_id"], stored["text"], markup)


def handle_reply_start(conn, callback, action_id):
    row, error = load_action(conn, action_id, ("reply", "topic"))
    if error:
        answer_callback(callback["id"], error)
        return
    message = callback.get("message") or {}
    targets = reply_targets(row)
    if not targets:
        answer_callback(callback["id"], "No replyable target was captured.")
        send_message(
            message["chat"]["id"],
            "No replyable target was captured for this topic yet. Open the topic in UiTM to reply manually.",
            reply_to_message_id=message.get("message_id"),
        )
        return
    answer_callback(callback["id"], "Choose reply target.")
    send_message(
        message["chat"]["id"],
        build_target_picker_text(row),
        reply_markup=build_target_keyboard(action_id, targets),
        reply_to_message_id=message.get("message_id"),
    )


def handle_reply_target(conn, callback, action_id, target_key):
    row, error = load_action(conn, action_id, ("reply", "topic"))
    if error:
        answer_callback(callback["id"], error)
        return
    target = find_reply_target(row, target_key)
    if not target:
        answer_callback(callback["id"], "Target expired or unavailable.")
        return
    message = callback.get("message") or {}
    user = callback.get("from") or {}
    pending_id, topic_title = start_reply_draft(
        conn,
        row,
        message["chat"]["id"],
        user.get("id"),
        action_id,
        target,
    )
    answer_callback(callback["id"], "Reply target selected.")
    post = target["post"]
    send_message(
        message["chat"]["id"],
        "\n".join([
            f"Reply draft for <b>{html_text(topic_title)}</b>",
            "",
            f"Target: <b>{html_text(target['label'])}</b>",
            html_text(post.get("author") or "Unknown"),
            html_text(truncate_text(post.get("content_excerpt"), 220)),
            "",
            "Type your reply message here, and send files to attach them.",
            "Send /cancel to stop.",
            "",
            f"Draft id: <code>{html_text(pending_id)}</code>",
        ]),
        reply_to_message_id=message.get("message_id"),
    )


def build_preview_text(row):
    lines = [
        f"Preview reply to <b>{html_text(row['topic_title'])}</b>",
        "",
        f"Target: <b>{html_text(row['target_label'] or 'Selected post')}</b>",
        html_text(row["target_author"] or "Unknown"),
    ]
    if row["target_excerpt"]:
        lines.append(html_text(row["target_excerpt"]))
    if row["target_posted_time"]:
        lines.append(html_text(row["target_posted_time"]))
    files = draft_attachments(row)
    lines.extend([
        "",
        "<b>Your reply</b>",
        html_text(row["draft_text"]) if (row["draft_text"] or "").strip() else "<i>(no text yet: type your message)</i>",
    ])
    if files:
        lines.append(f"📎 {len(files)} file(s): " + html_text(", ".join(item.get("name") or "file" for item in files)))
    lines.append("")
    lines.append("Send files (as documents for full quality) to attach them.")
    if POST_REPLIES_ENABLED:
        if REPLY_CONFIRM_BEFORE_SEND:
            lines.append("Confirm before sending to UiTM.")
        else:
            lines.append("Reply posting is enabled. Use Send to UiTM to continue.")
    else:
        lines.append("UiTM posting is disabled. You can save this as a draft.")
    return "\n".join(lines)


def build_preview_keyboard(pending_id, has_files=False):
    rows = [[
        {"text": "Send to UiTM", "callback_data": f"p:{pending_id}"},
        {"text": "✏️ Edit text", "callback_data": f"e:{pending_id}"},
    ]]
    if has_files:
        rows.append([{"text": "🗑 Remove files", "callback_data": f"rf:{pending_id}"}])
    rows.append([
        {"text": "Save Draft", "callback_data": f"s:{pending_id}"},
        {"text": "Cancel", "callback_data": f"c:{pending_id}"},
    ])
    return {"inline_keyboard": rows}


def send_preview(conn, chat_id, pending_id, reply_to_message_id=None):
    pending = load_pending(conn, pending_id)
    send_message(
        chat_id,
        build_preview_text(pending),
        reply_markup=build_preview_keyboard(pending_id, has_files=bool(draft_attachments(pending))),
        reply_to_message_id=reply_to_message_id,
    )


def telegram_file_from_message(message):
    document = message.get("document")
    if document:
        return {
            "file_id": document.get("file_id"),
            "file_unique_id": document.get("file_unique_id"),
            "name": document.get("file_name") or "file",
            "size": document.get("file_size") or 0,
            "mime": document.get("mime_type") or "application/octet-stream",
        }
    photos = message.get("photo") or []
    if photos:
        photo = max(photos, key=lambda item: item.get("file_size") or item.get("width", 0) * item.get("height", 0))
        return {
            "file_id": photo.get("file_id"),
            "file_unique_id": photo.get("file_unique_id"),
            "name": f"photo_{message.get('message_id')}.jpg",
            "size": photo.get("file_size") or 0,
            "mime": "image/jpeg",
        }
    return None


def handle_draft_file(conn, chat_id, message, pending, item):
    reply_to = message.get("message_id")
    attachments = draft_attachments(pending)
    if item["size"] > TELEGRAM_DOWNLOAD_MAX_BYTES:
        send_message(chat_id, f"📎 {html_text(item['name'])} is over 20 MB, the largest file a Telegram bot can download.", reply_to_message_id=reply_to)
        return
    if len(attachments) >= REPLY_MAX_FILES:
        send_message(chat_id, f"A reply can have at most {REPLY_MAX_FILES} files.", reply_to_message_id=reply_to)
        return
    if all(existing.get("file_unique_id") != item["file_unique_id"] for existing in attachments):
        attachments.append(item)
    draft_text = (message.get("caption") or "").strip() or pending["draft_text"] or ""
    update_pending_preview(conn, pending["id"], pending, draft_text, attachments)
    send_preview(conn, chat_id, pending["id"], reply_to)


def handle_remove_files(conn, callback, pending_id):
    row = load_pending(conn, pending_id)
    if not row or row["state"] not in ("preview", "awaiting_text"):
        answer_callback(callback["id"], "Draft expired or not found.")
        return
    update_pending_preview(conn, pending_id, row, row["draft_text"] or "", [])
    answer_callback(callback["id"], "Files removed.")
    send_preview(conn, callback["message"]["chat"]["id"], pending_id, callback["message"].get("message_id"))


def handle_save_draft(conn, callback, pending_id):
    row = load_pending(conn, pending_id)
    if not row:
        answer_callback(callback["id"], "Draft expired or not found.")
        return
    update_pending_state(conn, pending_id, "saved")
    answer_callback(callback["id"], "Draft saved.")
    send_message(
        callback["message"]["chat"]["id"],
        f"Draft saved for <b>{html_text(row['topic_title'])}</b>. No UiTM post was made.",
        reply_to_message_id=callback["message"].get("message_id"),
    )


def handle_edit_draft(conn, callback, pending_id):
    row = load_pending(conn, pending_id)
    if not row:
        answer_callback(callback["id"], "Draft expired or not found.")
        return
    update_pending_state(conn, pending_id, "awaiting_text")
    answer_callback(callback["id"], "Edit draft.")
    send_message(
        callback["message"]["chat"]["id"],
        "\n".join([
            f"Editing reply for <b>{html_text(row['topic_title'])}</b>",
            "",
            "Type the replacement reply message.",
            "Send /cancel to stop.",
        ]),
        reply_to_message_id=callback["message"].get("message_id"),
    )


# ---- posting replies to UiTM ------------------------------------------------

REPLY_URL_PATH = "/forumsv2/posts/reply"
POST_LOOKUP_PATH = "/forumsv2/posts/get_post_ajax"


def draft_attachments(row):
    try:
        items = json.loads(row["attachments_json"] or "[]")
    except (TypeError, ValueError, IndexError, KeyError):
        return []
    return items if isinstance(items, list) else []


def reply_html(text):
    """Plain Telegram text -> the paragraph HTML UiTM's editor produces."""
    paragraphs = [part for part in re.split(r"\n\s*\n", (text or "").strip()) if part.strip()]
    return "".join(
        "<p>" + "<br />".join(html.escape(line.strip(), quote=False) for line in part.splitlines()) + "</p>"
        for part in paragraphs
    )


def lookup_target_post(post_id):
    """UiTM's own lookup used by its Reply dialog; logs in again once if the session expired."""
    s = scraper()
    for attempt in (1, 2):
        session = uitm_session(fresh=attempt == 2)
        response = session.get(
            s.BASE_URL + POST_LOOKUP_PATH,
            params={"Id": post_id},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=30,
        )
        response.raise_for_status()
        try:
            post = (response.json() or {}).get("ChatPost") or {}
        except ValueError:
            if attempt == 2:
                raise s.ScraperError("UiTM did not return the reply target (session expired?)")
            continue
        if post.get("id") != post_id:
            raise s.ScraperError("UiTM returned a different post than the reply target")
        return session, post


def topic_posts_now(session, topic_url, topic_id):
    s = scraper()
    response = session.get(topic_url, timeout=30)
    response.raise_for_status()
    if urlparse_path(response.url).endswith("/login"):
        raise s.ScraperError("UiTM session expired while reading the topic")
    return s.extract_topic_posts(s.safe_soup(response.text), topic_id)


def urlparse_path(url):
    return scraper().urlparse(url or "").path.rstrip("/")


def download_telegram_file(item):
    s = scraper()
    info = telegram_post("getFile", {"file_id": item["file_id"]}) or {}
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        raise s.ScraperError(f"Telegram could not provide {item.get('name') or 'a file'}")
    try:
        response = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}", stream=True, timeout=60)
        response.raise_for_status()
        buffer = io.BytesIO()
        for chunk in response.iter_content(64 * 1024):
            buffer.write(chunk)
            if buffer.tell() > TELEGRAM_DOWNLOAD_MAX_BYTES:
                raise s.ScraperError(f"{item.get('name')} is larger than 20 MB")
        response.close()
    except requests.RequestException as error:
        raise s.ScraperError(f"could not download {item.get('name')} from Telegram ({type(error).__name__})")
    buffer.seek(0)
    return buffer


def find_posted_reply(posts, before_ids, parent_id, text):
    wanted = " ".join((text or "").split())[:40]
    for post in posts:
        if (
            post.get("is_own")
            and post.get("post_id") not in before_ids
            and post.get("parent_post_id") == parent_id
            and wanted in " ".join((post.get("content_excerpt") or "").split())
        ):
            return post
    return None


def submit_reply_to_ufuture(row):
    """Post the draft and confirm it on the forum.

    Returns {"status": "posted" | "not_sent" | "unverified", "message": str}.
    "not_sent" is only used when nothing can have been posted, so retrying is safe.
    """
    s = scraper()
    files = draft_attachments(row)
    try:
        session, target = lookup_target_post(row["target_post_id"])
        uploads = [(item, download_telegram_file(item)) for item in files]
        before_ids = {post["post_id"] for post in topic_posts_now(session, row["topic_url"], row["topic_id"])}
    except (s.ScraperError, requests.RequestException, ValueError) as error:
        return {"status": "not_sent", "message": s.failure_summary(error)}

    level = int(target.get("level") or 0)
    # Same rule as UiTM's Reply dialog: replies to a level-3 post go to its parent.
    parent_id = target.get("parent_id") if level == 3 and target.get("parent_id") else target["id"]
    data = {
        "_method": "POST",
        "data[ChatPost][content]": reply_html(row["draft_text"]),
        "replypost_id": parent_id,
        "replypost_level_id": str(level),
        "replypost_to": target.get("user_name") or "",
    }
    file_field = "data[ChatPostAttachment][files][]"
    parts = [(file_field, (item["name"], buffer, item.get("mime") or "application/octet-stream")) for item, buffer in uploads]
    parts = parts or [(file_field, ("", b"", "application/octet-stream"))]  # browsers send an empty part
    try:
        response = session.post(
            s.BASE_URL + REPLY_URL_PATH, data=data, files=parts,
            headers={"Referer": row["topic_url"]}, timeout=REPLY_POST_TIMEOUT_SECONDS, allow_redirects=True,
        )
        rejected = response.status_code >= 400 or urlparse_path(response.url).endswith("/login")
    except requests.RequestException as error:
        response, rejected = None, False
        print(f"UiTM reply POST error: {type(error).__name__}")

    time.sleep(2)
    try:
        posts = topic_posts_now(session, row["topic_url"], row["topic_id"])
    except (s.ScraperError, requests.RequestException) as error:
        return {"status": "unverified", "message": f"couldn't re-read the topic ({s.failure_summary(error)})"}
    posted = find_posted_reply(posts, before_ids, parent_id, row["draft_text"])
    if not posted:
        if rejected:
            return {"status": "not_sent", "message": f"UiTM rejected the reply (HTTP {response.status_code})"}
        return {"status": "unverified", "message": "the reply did not appear on the topic page", "posts": posts}
    message = "Posted to UiTM."
    if len(posted.get("attachments") or []) < len(files):
        message += f" Only {len(posted.get('attachments') or [])} of {len(files)} file(s) appear on the post."
    return {"status": "posted", "message": message, "posts": posts}


def refresh_topic_in_db(conn, row, posts):
    """Store the topic as just re-read after posting, so the bot shows the new reply right away."""
    s = scraper()
    topic = conn.execute(
        "SELECT * FROM topics WHERE topic_id = ? ORDER BY last_updated DESC LIMIT 1", (row["topic_id"],)
    ).fetchone()
    if not topic or not posts:
        return
    topic_row = {key: topic[key] for key in topic.keys()}
    topic_row["week"] = topic_row["week_title"]
    topic_row["replies"] = max(0, len(posts) - 1)
    s.save_to_db(conn, [], {}, [topic_row], posts)


def reply_rate_limited(conn):
    last = get_state(conn, "last_reply_posted_at")
    if not last:
        return 0
    try:
        waited = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return 0
    return max(0, int(REPLY_MIN_INTERVAL_SECONDS - waited))


def handle_send_reply(conn, callback, pending_id):
    message = callback.get("message") or {}
    chat_id, message_id = message["chat"]["id"], message.get("message_id")
    row = load_pending(conn, pending_id)
    if not row:
        answer_callback(callback["id"], "Draft expired or not found.")
        return
    if row["state"] != "preview":
        answer_callback(callback["id"], "Draft is not ready to send.")
        return
    if not (row["draft_text"] or "").strip():
        answer_callback(callback["id"], "Add a message first.")
        send_message(chat_id, "Type your reply message before sending; files alone can't be posted.", reply_to_message_id=message_id)
        return
    if row["dedupe_key"]:
        duplicate = conn.execute('''SELECT id FROM telegram_pending_replies
                                    WHERE dedupe_key = ?
                                      AND state IN ('posted', 'unverified')
                                      AND id != ?
                                    LIMIT 1''',
                                 (row["dedupe_key"], pending_id)).fetchone()
        if duplicate:
            answer_callback(callback["id"], "Duplicate reply blocked.")
            send_message(
                chat_id,
                "Duplicate reply blocked. This exact reply was already sent to the selected post.",
                reply_to_message_id=message_id,
            )
            return
    if not POST_REPLIES_ENABLED:
        answer_callback(callback["id"], "UiTM posting disabled.")
        send_message(
            chat_id,
            "\n".join([
                "UiTM posting is disabled by <code>UFUTURE_REPLY_ENABLED=false</code>.",
                "Your draft is still available. Use Save Draft, Edit, or Cancel.",
            ]),
            reply_to_message_id=message_id,
        )
        return
    wait = reply_rate_limited(conn)
    if wait:
        answer_callback(callback["id"], f"Please wait {wait}s before posting again.")
        return
    # Claim the draft so a double tap can't post twice.
    claimed = conn.execute(
        "UPDATE telegram_pending_replies SET state = 'sending', updated_at = ? WHERE id = ? AND state = 'preview'",
        (datetime.now().isoformat(), pending_id),
    ).rowcount
    conn.commit()
    if not claimed:
        answer_callback(callback["id"], "Already sending.")
        return
    answer_callback(callback["id"], "Posting to UiTM…")

    result = submit_reply_to_ufuture(row)
    open_button = {"inline_keyboard": [[{"text": "🌐 Open in UiTM", "url": row["topic_url"]}]]} if row["topic_url"] else None
    files = draft_attachments(row)
    if result["status"] == "posted":
        update_pending_result(conn, pending_id, "posted", result["message"])
        try:
            refresh_topic_in_db(conn, row, result.get("posts"))
        except Exception as error:
            print(f"Topic refresh after posting failed: {type(error).__name__}")
        set_state(conn, "last_reply_posted_at", datetime.now().isoformat())
        lines = [f"✅ <b>Posted to UiTM</b> · {html_text(row['topic_title'])}", f"Reply to {html_text(row['target_label'] or 'selected post')}"]
        if files:
            lines.append(f"📎 {len(files)} file(s) attached")
        if result["message"] != "Posted to UiTM.":
            lines.append(f"⚠️ {html_text(result['message'])}")
        send_message(chat_id, "\n".join(lines), reply_markup=open_button, reply_to_message_id=message_id)
    elif result["status"] == "not_sent":
        update_pending_result(conn, pending_id, "preview", result["message"])
        send_message(
            chat_id,
            f"❌ Not posted: {html_text(result['message'])}\nYour draft is unchanged; tap Send to UiTM to try again.",
            reply_to_message_id=message_id,
        )
    else:
        update_pending_result(conn, pending_id, "unverified", result["message"])
        set_state(conn, "last_reply_posted_at", datetime.now().isoformat())
        send_message(
            chat_id,
            f"⚠️ The reply was submitted, but I couldn't confirm it: {html_text(result['message'])}.\n"
            "Check the topic in UiTM before sending it again.",
            reply_markup=open_button,
            reply_to_message_id=message_id,
        )


def handle_cancel(conn, callback, pending_id):
    row = load_pending(conn, pending_id)
    if row:
        update_pending_state(conn, pending_id, "cancelled")
    answer_callback(callback["id"], "Cancelled.")
    send_message(
        callback["message"]["chat"]["id"],
        "Reply draft cancelled.",
        reply_to_message_id=callback["message"].get("message_id"),
    )


# ============================================================================
# Commands, browsing and files
# Reuses the scraper module for UiTM login, downloads and formatting.
# ============================================================================

SCRAPER_FILE = os.getenv(
    "SCRAPER_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ufuture-scraper.py")
)
LIST_LIMIT = 20
BOT_COMMANDS = [
    ("latest", "Newest forum posts"),
    ("courses", "Browse courses, weeks and topics"),
    ("files", "Get attachments from a course"),
    ("status", "Scraper health and counts"),
    ("mute", "Mute a course (replies to you still notify)"),
    ("unmute", "Unmute a course"),
    ("help", "What this bot can do"),
    ("cancel", "Cancel the current reply draft"),
]
HELP_TEXT = "\n".join([
    "<b>UiTM forum bot</b>",
    "",
    "/latest: newest forum posts",
    "/courses: browse courses, weeks and topics",
    "/files: get attachments from a course",
    "/status: scraper health and counts",
    "/mute, /unmute: silence a course (replies to you still notify)",
    "/cancel: cancel a reply draft",
    "",
    "Tap ▸ on a notification to open its topic card, then ✍️ Reply to draft a reply.",
])
_scraper_module = None
_uitm_session = None


def scraper():
    global _scraper_module
    if _scraper_module is None:
        spec = importlib.util.spec_from_file_location("ufuture_scraper", SCRAPER_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _scraper_module = module
    return _scraper_module


def show(chat_id, message_id, text, reply_markup=None):
    """Edit the message when navigating from a button, otherwise send a new one."""
    if message_id:
        return edit_message(chat_id, message_id, text, reply_markup)
    return send_message(chat_id, text, reply_markup=reply_markup)


def button_rows(buttons, per_row):
    return [buttons[index:index + per_row] for index in range(0, len(buttons), per_row)]


def week_sort_key(week_title):
    match = re.search(r"week\s*(\d+)", week_title or "", re.IGNORECASE)
    return (int(match.group(1)) if match else 999, week_title or "")


def course_label(row):
    name = scraper().course_display_name({"course_code": row["course_code"], "course_name": row["course_name"]})
    return truncate_text(f"{row['course_code']} · {name.title()}", 40)


# ---- topic views built from the database ----------------------------------

def topic_view(conn, topic_id):
    s = scraper()
    row = conn.execute('''SELECT t.course_code, t.week_title, t.topic_id, t.topic_title, t.topic_url,
                                 t.created_by, t.replies, t.last_activity, c.course_name
                          FROM topics t LEFT JOIN courses c ON c.course_code = t.course_code
                          WHERE t.topic_id = ?
                          ORDER BY t.last_updated DESC
                          LIMIT 1''', (topic_id,)).fetchone()
    if not row:
        return None, None
    topic = {
        "topic_id": row["topic_id"],
        "topic_title": row["topic_title"],
        "topic_url": row["topic_url"],
        "created_by": row["created_by"],
        "replies": row["replies"] or 0,
        "last_activity": row["last_activity"],
    }
    posts = s.annotate_topic_posts(topic, s.get_current_post_state(conn, [topic_id]))
    others = [post for post in posts if not post.get("is_own")]
    detail = s.build_topic_detail(
        topic, posts, [], is_new_topic=False, exact_post_diff=True, display_posts=others[-10:],
    )
    change = {
        "course_code": row["course_code"],
        "course_name": row["course_name"] or row["course_code"],
        "week": row["week_title"],
    }
    return change, detail


def create_topic_action(conn, topic_id, back):
    s = scraper()
    change, detail = topic_view(conn, topic_id)
    if not detail:
        return None
    payload_topic = dict(detail, card_text=s.build_topic_card_text(change, detail), back=back)
    action_id = s.create_telegram_action(conn, "topic", change, payload_topic)
    conn.commit()
    return action_id


def attachment_rows(conn, where, params):
    s = scraper()
    rows = conn.execute(f'''SELECT p.attachments_json, p.posted_time, p.topic_id,
                                   t.course_code, t.week_title, t.topic_title
                            FROM posts p JOIN topics t ON t.topic_id = p.topic_id
                            WHERE {where}''', params).fetchall()
    attachments, seen = [], set()
    for row in rows:
        for attachment in s.parse_attachments_json(row["attachments_json"]):
            url = attachment.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            attachments.append({
                "url": url,
                "label": attachment.get("label") or s.media_label_from_url(url),
                "posted_time": row["posted_time"],
                "topic_id": row["topic_id"],
                "course_code": row["course_code"],
                "week": row["week_title"],
                "topic_title": row["topic_title"],
            })
    attachments.sort(key=lambda item: s.parse_posted_time(item["posted_time"]) or datetime.min, reverse=True)
    return attachments


def file_buttons(conn, attachments):
    s = scraper()
    buttons = []
    for index, attachment in enumerate(attachments, start=1):
        change = {"course_code": attachment["course_code"], "week": attachment["week"]}
        action_id = s.create_telegram_action(
            conn, "file", change, {"topic_id": attachment["topic_id"], "attachment": attachment},
        )
        buttons.append({"text": f"📎 {index}", "callback_data": f"f:{action_id}"})
    conn.commit()
    return buttons


def file_list_lines(attachments):
    s = scraper()
    lines = []
    for index, attachment in enumerate(attachments, start=1):
        lines.append(f"{index}. {html_text(attachment['label'])}")
        lines.append(
            f"   {html_text(s.short_week(attachment['week']))} · {html_text(attachment['topic_title'])}"
            f" · {html_text(s.absolute_time(attachment['posted_time']))}"
        )
    return lines


# ---- renderers (text, keyboard) ---------------------------------------------

def render_courses(conn, *, for_files=False):
    rows = conn.execute("SELECT course_code, course_name FROM courses ORDER BY course_code").fetchall()
    if not rows:
        return "No courses have been scraped yet.", None
    prefix = "fs" if for_files else "cs"
    title = "📎 <b>Files</b>: pick a course" if for_files else "📚 <b>Courses</b>"
    buttons = [{"text": course_label(row), "callback_data": f"{prefix}:{row['course_code']}"} for row in rows]
    return title, {"inline_keyboard": button_rows(buttons, 1)}


def render_course_weeks(conn, course_code):
    s = scraper()
    weeks = [
        row for row in conn.execute(
            "SELECT id, week_title, topics, posts FROM weeks WHERE course_code = ?", (course_code,)
        ).fetchall()
        if s.parse_count(row["topics"]) > 0
    ]
    weeks.sort(key=lambda row: week_sort_key(row["week_title"]))
    lines = [f"📚 <b>{html_text(course_code)}</b>"]
    if not weeks:
        lines.append("No topics yet.")
    for row in weeks:
        topics = s.parse_count(row["topics"])
        posts = s.parse_count(row["posts"])
        lines.append(
            f"{html_text(s.short_week(row['week_title']))} · {s.format_count(topics, 'topic')}"
            f" · {s.format_count(posts, 'post')}"
        )
    buttons = [{"text": s.short_week(row["week_title"]), "callback_data": f"cw:{row['id']}"} for row in weeks]
    rows = button_rows(buttons, 4) + [[{"text": "« Courses", "callback_data": "cc:"}]]
    return "\n".join(lines), {"inline_keyboard": rows}


def render_week_topics(conn, week_id):
    s = scraper()
    week = conn.execute("SELECT course_code, week_title FROM weeks WHERE id = ?", (week_id,)).fetchone()
    if not week:
        return "This week is no longer available.", {"inline_keyboard": [[{"text": "« Courses", "callback_data": "cc:"}]]}
    topics = conn.execute('''SELECT topic_id, topic_title, replies, last_activity FROM topics
                             WHERE course_code = ? AND week_title = ?
                             ORDER BY topic_title''', (week["course_code"], week["week_title"])).fetchall()
    lines = [f"📚 <b>{html_text(week['course_code'])}</b> · {html_text(s.short_week(week['week_title']))}"]
    buttons = []
    for row in topics:
        lines.append(
            f"🧵 {html_text(row['topic_title'])} · {s.format_count(row['replies'] or 0, 'reply', 'replies')}"
        )
        action_id = create_topic_action(conn, row["topic_id"], back=f"cw:{week_id}")
        if action_id:
            buttons.append({"text": f"{truncate_text(row['topic_title'], 30)} ▸", "callback_data": f"o:{action_id}"})
    if not topics:
        lines.append("No topic details stored for this week yet.")
    rows = button_rows(buttons, 1) + [[{"text": f"« {week['course_code']}", "callback_data": f"cs:{week['course_code']}"}]]
    return "\n".join(lines), {"inline_keyboard": rows}


def render_latest(conn, limit=5):
    s = scraper()
    rows = conn.execute('''SELECT DISTINCT p.topic_id, p.post_id, p.author, p.content_excerpt, p.posted_time,
                                  p.is_own, t.course_code, t.week_title, t.topic_title
                           FROM posts p JOIN topics t ON t.topic_id = p.topic_id''').fetchall()
    rows = [row for row in rows if not row["is_own"]]
    rows.sort(key=lambda row: s.parse_posted_time(row["posted_time"]) or datetime.min, reverse=True)
    rows = rows[:limit]
    if not rows:
        return "No forum posts stored yet.", None
    lines = ["🕐 <b>Latest forum posts</b>"]
    buttons, topic_actions = [], {}
    for index, row in enumerate(rows, start=1):
        lines.append("")
        lines.append(
            f"{index}. {html_text(row['course_code'])} {html_text(s.short_week(row['week_title']))}"
            f" · <b>{html_text(row['topic_title'])}</b>"
        )
        lines.append(
            f"   {html_text(s.short_person(row['author']))} · {html_text(s.relative_time(row['posted_time']))}:"
            f" {html_text(truncate_text(row['content_excerpt'], 90))}"
        )
        if row["topic_id"] not in topic_actions:
            topic_actions[row["topic_id"]] = create_topic_action(conn, row["topic_id"], back="lt:")
        if topic_actions[row["topic_id"]]:
            buttons.append({"text": f"{index} ▸", "callback_data": f"o:{topic_actions[row['topic_id']]}"})
    return "\n".join(lines), {"inline_keyboard": button_rows(buttons, 5)}


def render_course_files(conn, course_code):
    attachments = attachment_rows(conn, "t.course_code = ?", (course_code,))[:LIST_LIMIT]
    back = [[{"text": "« Courses", "callback_data": "fc:"}]]
    if not attachments:
        return f"📎 <b>{html_text(course_code)}</b>\nNo attachments found.", {"inline_keyboard": back}
    lines = [f"📎 <b>{html_text(course_code)} files</b>", ""] + file_list_lines(attachments)
    rows = button_rows(file_buttons(conn, attachments), 5) + back
    return clip_html_lines(lines, DETAILS_MAX_LENGTH), {"inline_keyboard": rows}


def render_topic_files(conn, topic_action_id):
    row, error = load_action(conn, topic_action_id, "topic")
    if error:
        return error, None
    attachments = attachment_rows(conn, "p.topic_id = ?", (row["topic_id"],))[:LIST_LIMIT]
    topic = action_payload(row).get("topic", {})
    back = [[{"text": "« Back", "callback_data": f"o:{topic_action_id}"}]]
    lines = [f"📎 <b>{html_text(topic.get('topic_title') or 'Topic')} files</b>", ""]
    if not attachments:
        return "\n".join(lines + ["No attachments found."]), {"inline_keyboard": back}
    rows = button_rows(file_buttons(conn, attachments), 5) + back
    return clip_html_lines(lines + file_list_lines(attachments), DETAILS_MAX_LENGTH), {"inline_keyboard": rows}


def parse_bot_time(value):
    try:
        return datetime.strptime(value or "", "%d/%m/%Y %I:%M %p")
    except ValueError:
        return None


def render_status(conn):
    s = scraper()
    last_ok = get_state(conn, "last_success_at")
    last_fail = get_state(conn, "last_failure_at")
    counts = {
        name: conn.execute(query).fetchone()[0]
        for name, query in {
            "courses": "SELECT count(*) FROM courses",
            "topics": "SELECT count(DISTINCT topic_id) FROM topics",
            "posts": "SELECT count(*) FROM posts",
        }.items()
    }
    files = dict(conn.execute("SELECT status, count(*) FROM attachment_queue GROUP BY status").fetchall())
    muted = sorted(s.muted_courses(conn))
    lines = ["📊 <b>Forum bot status</b>", ""]
    lines.append(f"✅ Last successful check: {html_text(s.relative_time(last_ok)) if last_ok else 'none recorded yet'}")
    if last_fail and (not last_ok or (parse_bot_time(last_fail) or datetime.min) > (parse_bot_time(last_ok) or datetime.min)):
        reason = get_state(conn, "last_failure_reason") or "unknown"
        lines.append(f"⚠️ Last failure: {html_text(s.relative_time(last_fail))} · {html_text(reason)}")
    lines.append(
        f"📚 {s.format_count(counts['courses'], 'course')} · {s.format_count(counts['topics'], 'topic')}"
        f" · {s.format_count(counts['posts'], 'post')} tracked"
    )
    lines.append(
        f"📎 Files sent: {files.get('sent', 0)} · waiting: {files.get('pending', 0)}"
        f" · too large: {files.get('too_large', 0)} · failed: {files.get('failed', 0)}"
    )
    lines.append(f"🔕 Muted: {html_text(', '.join(muted))}" if muted else "🔔 No muted courses")
    lines.append("")
    lines.append("The forum is checked every 10 minutes.")
    return "\n".join(lines), None


def render_mute_picker(conn, *, unmute):
    s = scraper()
    muted = s.muted_courses(conn)
    rows = conn.execute("SELECT course_code, course_name FROM courses ORDER BY course_code").fetchall()
    rows = [row for row in rows if (row["course_code"] in muted) == unmute]
    if not rows:
        return ("No courses are muted." if unmute else "All courses are already muted."), None
    prefix = "um" if unmute else "mu"
    title = "🔔 Unmute which course?" if unmute else "🔕 Mute which course? Replies to your posts will still notify."
    buttons = [{"text": course_label(row), "callback_data": f"{prefix}:{row['course_code']}"} for row in rows]
    return title, {"inline_keyboard": button_rows(buttons, 1)}


def set_course_muted(conn, course_code, muted):
    s = scraper()
    course_code = (course_code or "").strip().upper()
    if not conn.execute("SELECT 1 FROM courses WHERE course_code = ?", (course_code,)).fetchone():
        return f"Unknown course <b>{html_text(course_code)}</b>. Use /courses to see your courses."
    codes = s.muted_courses(conn)
    codes = codes | {course_code} if muted else codes - {course_code}
    set_state(conn, "muted_courses", json.dumps(sorted(codes)))
    if muted:
        return f"🔕 <b>{html_text(course_code)}</b> muted. You'll still be notified when someone replies to your posts."
    return f"🔔 <b>{html_text(course_code)}</b> unmuted."


# ---- files ---------------------------------------------------------------------

def uitm_session(fresh=False):
    global _uitm_session
    if _uitm_session is None or fresh:
        s = scraper()
        session = requests.Session()
        session.headers.update(s.HEADERS)
        s.login(session)
        _uitm_session = session
    return _uitm_session


def fetch_attachment(url, fallback_name):
    """Download into memory with the bot's own UiTM session, logging in again once if it expired."""
    s = scraper()
    for attempt in (1, 2):
        try:
            response = s.open_attachment(uitm_session(fresh=attempt == 2), url)
            break
        except s.LoginError:
            raise
        except s.ScraperError:
            if attempt == 2:
                raise
    try:
        filename = s.attachment_filename(response, fallback_name)
        buffer = s.download_attachment(response, int(s.ATTACHMENT_MAX_MB * 1024 * 1024))
    finally:
        response.close()
    return filename, buffer


def send_document(chat_id, document, caption, *, filename=None, reply_to=None):
    data = {"chat_id": chat_id, "caption": caption}
    if reply_to:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    files = None
    if filename is None:
        data["document"] = document  # Telegram file_id of a file sent before
    else:
        files = {"document": (filename, document)}
    try:
        response = requests.post(f"{API_BASE}/sendDocument", data=data, files=files, timeout=120)
    except requests.RequestException as error:
        print(f"Telegram sendDocument error: {type(error).__name__}")
        return None
    if response.status_code != 200:
        print(f"Telegram sendDocument failed: HTTP {response.status_code}")
        return None
    return response.json().get("result")


def remember_sent_file(conn, attachment, size_bytes, file_id):
    now = datetime.now().isoformat()
    conn.execute('''INSERT INTO attachment_queue
                    (url, label, course_code, topic_id, topic_title, post_id, reply_to_message_id,
                     silent, status, attempts, size_bytes, telegram_file_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, '', '', 0, 'sent', 1, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        status = 'sent', size_bytes = excluded.size_bytes,
                        telegram_file_id = excluded.telegram_file_id, updated_at = excluded.updated_at''',
                 (attachment["url"], attachment.get("label") or "", attachment.get("course_code") or "",
                  attachment.get("topic_id") or "", attachment.get("topic_title") or "",
                  size_bytes, file_id, now, now))
    conn.commit()


def handle_send_file(conn, callback, action_id):
    s = scraper()
    row, error = load_action(conn, action_id, "file")
    if error:
        answer_callback(callback["id"], error)
        return
    attachment = action_payload(row).get("topic", {}).get("attachment") or {}
    message = callback.get("message") or {}
    chat_id, message_id = message["chat"]["id"], message.get("message_id")
    label = attachment.get("label") or "attachment"
    caption = truncate_text(f"📎 {label}\n{attachment.get('course_code', '')} · {attachment.get('topic_title', '')}", 1000)

    known = conn.execute(
        "SELECT telegram_file_id FROM attachment_queue WHERE url = ? AND COALESCE(telegram_file_id, '') != ''",
        (attachment.get("url"),),
    ).fetchone()
    if known:
        answer_callback(callback["id"], "Sending…")
        if send_document(chat_id, known["telegram_file_id"], caption, reply_to=message_id):
            return
    else:
        answer_callback(callback["id"], "Fetching from UiTM…")

    try:
        filename, buffer = fetch_attachment(attachment["url"], label)
    except s.AttachmentTooLarge as too_large:
        send_message(
            chat_id,
            f"📎 {html_text(label)} is too large to send here ({s.format_size(too_large.size_bytes)},"
            f" limit {s.ATTACHMENT_MAX_MB:g} MB). Open it in UiTM.",
            reply_markup={"inline_keyboard": [[{"text": "Open in UiTM", "url": attachment["url"]}]]},
            reply_to_message_id=message_id,
        )
        return
    except (s.ScraperError, requests.RequestException) as fetch_error:
        send_message(
            chat_id,
            f"Couldn't fetch {html_text(label)} from UiTM: {html_text(s.failure_summary(fetch_error))}",
            reply_to_message_id=message_id,
        )
        return
    result = send_document(chat_id, buffer, caption, filename=filename, reply_to=message_id)
    if not result:
        send_message(chat_id, f"Telegram rejected the upload of {html_text(label)}.", reply_to_message_id=message_id)
        return
    remember_sent_file(conn, attachment, buffer.getbuffer().nbytes, (result.get("document") or {}).get("file_id"))


# ---- command and navigation routing -------------------------------------------

def handle_command(conn, chat_id, command, argument):
    if command in {"help", "start"}:
        show(chat_id, None, HELP_TEXT)
    elif command == "latest":
        show(chat_id, None, *render_latest(conn))
    elif command == "courses":
        show(chat_id, None, *render_courses(conn))
    elif command == "files":
        show(chat_id, None, *render_courses(conn, for_files=True))
    elif command == "status":
        show(chat_id, None, *render_status(conn))
    elif command in {"mute", "unmute"}:
        if argument:
            show(chat_id, None, set_course_muted(conn, argument, command == "mute"))
        else:
            show(chat_id, None, *render_mute_picker(conn, unmute=command == "unmute"))
    else:
        show(chat_id, None, "Unknown command.\n\n" + HELP_TEXT)


NAVIGATION_RENDERERS = {
    "cc": lambda conn, value: render_courses(conn),
    "cs": render_course_weeks,
    "cw": lambda conn, value: render_week_topics(conn, int(value)),
    "lt": lambda conn, value: render_latest(conn),
    "fc": lambda conn, value: render_courses(conn, for_files=True),
    "fs": render_course_files,
    "tf": render_topic_files,
}


def handle_navigation(conn, callback, prefix, value):
    message = callback.get("message") or {}
    answer_callback(callback["id"])
    text, markup = NAVIGATION_RENDERERS[prefix](conn, value)
    show(message["chat"]["id"], message.get("message_id"), text, markup)


def handle_mute_button(conn, callback, course_code, muted):
    message = callback.get("message") or {}
    answer_callback(callback["id"], "Muted." if muted else "Unmuted.")
    show(message["chat"]["id"], message.get("message_id"), set_course_muted(conn, course_code, muted))


def register_bot_commands():
    commands = [{"command": name, "description": description} for name, description in BOT_COMMANDS]
    telegram_post("setMyCommands", {"commands": json.dumps(commands)})


def handle_callback(conn, callback):
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (callback.get("from") or {}).get("id")
    if not is_allowed_chat(chat_id):
        answer_callback(callback["id"], "Unauthorized chat.")
        print("Ignored callback from unauthorized chat")
        return
    if not is_allowed_user(user_id):
        answer_callback(callback["id"], "Unauthorized user.")
        print("Ignored callback from unauthorized user")
        return
    data = callback.get("data") or ""
    if ":" not in data:
        answer_callback(callback["id"], "Invalid action.")
        return
    prefix, identifier = data.split(":", 1)
    if prefix in NAVIGATION_RENDERERS:
        handle_navigation(conn, callback, prefix, identifier)
    elif prefix == "f":
        handle_send_file(conn, callback, identifier)
    elif prefix == "rf":
        handle_remove_files(conn, callback, identifier)
    elif prefix in {"mu", "um"}:
        handle_mute_button(conn, callback, identifier, prefix == "mu")
    elif prefix == "o":
        handle_open_card(conn, callback, identifier)
    elif prefix == "cd":
        handle_card_details(conn, callback, identifier)
    elif prefix == "bm":
        handle_back_to_message(conn, callback)
    elif prefix == "d":
        handle_details(conn, callback, identifier)
    elif prefix == "r":
        handle_reply_start(conn, callback, identifier)
    elif prefix == "t":
        parts = identifier.split(":", 1)
        if len(parts) != 2:
            answer_callback(callback["id"], "Invalid target.")
            return
        handle_reply_target(conn, callback, parts[0], parts[1])
    elif prefix == "p":
        handle_send_reply(conn, callback, identifier)
    elif prefix == "e":
        handle_edit_draft(conn, callback, identifier)
    elif prefix == "s":
        handle_save_draft(conn, callback, identifier)
    elif prefix == "c":
        handle_cancel(conn, callback, identifier)
    elif prefix == "x":
        answer_callback(callback["id"], "Cancelled.")
    else:
        answer_callback(callback["id"], "Unknown action.")


def handle_message(conn, message):
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (message.get("from") or {}).get("id")
    if not is_allowed_chat(chat_id):
        print("Ignored message from unauthorized chat")
        return
    if not is_allowed_user(user_id):
        print("Ignored message from unauthorized user")
        return
    text = (message.get("text") or "").strip()
    incoming_file = telegram_file_from_message(message)
    if not text and not incoming_file:
        return
    if incoming_file:
        pending = latest_open_reply(conn, chat_id, user_id)
        if not pending:
            send_message(
                chat_id,
                "To attach a file to a forum reply, tap ✍️ Reply on a topic first, then send the file.",
                reply_to_message_id=message.get("message_id"),
            )
            return
        handle_draft_file(conn, chat_id, message, pending, incoming_file)
        return
    if text.startswith("/"):
        command, _, argument = text[1:].partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "cancel":
            pending = latest_open_reply(conn, chat_id, user_id)
            if pending:
                update_pending_state(conn, pending["id"], "cancelled")
                send_message(chat_id, "Reply draft cancelled.", reply_to_message_id=message.get("message_id"))
            else:
                send_message(chat_id, "Nothing to cancel.", reply_to_message_id=message.get("message_id"))
            return
        handle_command(conn, chat_id, command, argument.strip())
        return
    pending = latest_open_reply(conn, chat_id, user_id)
    # Text fills the draft while it is awaiting text, or a preview that only has files so far.
    if pending and (pending["state"] == "awaiting_text" or not (pending["draft_text"] or "").strip()):
        update_pending_preview(conn, pending["id"], pending, text)
        send_preview(conn, chat_id, pending["id"], message.get("message_id"))
        return
    if pending:
        send_message(
            chat_id,
            "Your reply is in the preview above. Tap ✏️ Edit text to change it, or send a file to attach it.",
            reply_to_message_id=message.get("message_id"),
        )
        return
    send_message(
        chat_id,
        "No reply draft is open. Tap ✍️ Reply on a topic to start one, or see /help.",
        reply_to_message_id=message.get("message_id"),
    )


def poll_updates(conn):
    offset_state = get_state(conn, "telegram_update_offset")
    if offset_state is None and DROP_PENDING_UPDATES:
        offset = bootstrap_update_offset(conn)
    else:
        offset = int(offset_state or "0")
    cleanup_counter = 0
    print("Telegram interactive bot started")
    sys.stdout.flush()
    while RUNNING:
        try:
            response = requests.get(
                f"{API_BASE}/getUpdates",
                params={
                    "timeout": 30,
                    "offset": offset,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
                timeout=35,
            )
            if response.status_code != 200:
                print(f"Telegram getUpdates failed: HTTP {response.status_code}")
                time.sleep(5)
                continue
            for update in response.json().get("result", []):
                offset = max(offset, update["update_id"] + 1)
                try:
                    if "callback_query" in update:
                        handle_callback(conn, update["callback_query"])
                    elif "message" in update:
                        handle_message(conn, update["message"])
                except Exception as error:
                    print(f"Telegram update handling failed: {error}")
                set_state(conn, "telegram_update_offset", offset)
            cleanup_counter += 1
            if cleanup_counter >= 20:
                cleanup_expired(conn)
                cleanup_counter = 0
        except requests.RequestException as error:
            print(f"Telegram polling error: {error}")
            time.sleep(5)


def bootstrap_update_offset(conn):
    try:
        response = requests.get(
            f"{API_BASE}/getUpdates",
            params={
                "timeout": 0,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
            timeout=10,
        )
        if response.status_code != 200:
            print(f"Telegram bootstrap getUpdates failed: HTTP {response.status_code}")
            return 0
        updates = response.json().get("result", [])
        offset = max((update["update_id"] for update in updates), default=-1) + 1
        set_state(conn, "telegram_update_offset", offset)
        if updates:
            print(f"Dropped {len(updates)} pending Telegram update(s) on first bot start")
        return offset
    except requests.RequestException as error:
        print(f"Telegram bootstrap polling error: {error}")
        return 0


def main():
    if not INTERACTIVE_ENABLED:
        print("Telegram interactive bot disabled")
        return 0
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram interactive bot missing required Telegram config")
        return 2
    conn = connect_db()
    init_db(conn)
    try:
        # Make sure every table the commands read (courses, posts, attachment_queue, ...) exists.
        scraper().init_db().close()
    except Exception as error:
        print(f"Scraper module unavailable; commands will be limited: {type(error).__name__}")
    register_bot_commands()
    try:
        poll_updates(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
