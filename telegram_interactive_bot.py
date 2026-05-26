import html
import json
import os
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
POST_REPLIES_ENABLED = os.getenv("UFUTURE_POST_REPLIES", "false").strip().lower() in {"1", "true", "yes", "on"}
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
        topic_title TEXT,
        draft_text TEXT,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_bot_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()


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
    conn.commit()


def is_allowed_chat(chat_id):
    return TG_CHAT_ID and str(chat_id) == str(TG_CHAT_ID)


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


def answer_callback(callback_id, text=None):
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    telegram_post("answerCallbackQuery", data)


def load_action(conn, action_id, expected_type=None):
    row = conn.execute("SELECT * FROM telegram_actions WHERE action_id = ?", (action_id,)).fetchone()
    if not row:
        return None, "Action expired or not found."
    if expected_type and row["action_type"] != expected_type:
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


def build_details_text(row):
    payload = action_payload(row)
    change = payload.get("change", {})
    topic = payload.get("topic", {})
    root_post = topic.get("root_post") or {}
    posts = topic.get("detail_posts") or topic.get("posts", []) or []
    course_name = str(change.get("course_name") or "")
    course_code = str(change.get("course_code") or "")
    if course_code and course_name.upper().startswith(course_code.upper()):
        course_name = course_name[len(course_code):].strip(" -:") or course_name
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
    reply_posts = [post for post in posts if post.get("post_id") != root_post_id]
    if reply_posts:
        lines.extend(["", "<b>New/latest replies</b>"])
        for index, post in enumerate(reply_posts[:10], start=1):
            lines.append(f"{index}. {html_text(post.get('author') or 'Unknown')}")
            if post.get("content_excerpt"):
                lines.append(f"   {html_text(post.get('content_excerpt'))}")
            if post.get("posted_time"):
                lines.append(f"   {html_text(post.get('posted_time'))}")
    elif not root_post:
        lines.extend(["", "No stored reply excerpt is available for this topic."])
    if row["topic_url"]:
        lines.extend(["", f"Open topic: {html_text(row['topic_url'])}"])
    return "\n".join(lines)


def start_reply_draft(conn, row, chat_id, user_id):
    payload = action_payload(row)
    topic = payload.get("topic", {})
    now = datetime.now()
    pending_id = secrets.token_urlsafe(8)
    expires_at = now + timedelta(hours=max(1, REPLY_DRAFT_TTL_HOURS))
    conn.execute('''INSERT INTO telegram_pending_replies
                   (id, chat_id, user_id, topic_id, post_id, topic_title,
                    draft_text, state, created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (pending_id, str(chat_id), str(user_id), row["topic_id"] or "",
                  row["post_id"] or "", topic.get("topic_title") or "Forum topic",
                  "", "awaiting_text", now.isoformat(), now.isoformat(), expires_at.isoformat()))
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


def handle_reply_start(conn, callback, action_id):
    row, error = load_action(conn, action_id, "reply")
    if error:
        answer_callback(callback["id"], error)
        return
    message = callback.get("message") or {}
    user = callback.get("from") or {}
    pending_id, topic_title = start_reply_draft(conn, row, message["chat"]["id"], user.get("id"))
    answer_callback(callback["id"], "Reply draft started.")
    send_message(
        message["chat"]["id"],
        "\n".join([
            f"Reply draft for <b>{html_text(topic_title)}</b>",
            "",
            "Type your reply message here.",
            "Send /cancel to stop.",
            "",
            f"Draft id: <code>{html_text(pending_id)}</code>",
        ]),
        reply_to_message_id=message.get("message_id"),
    )


def handle_save_draft(conn, callback, pending_id):
    row = load_pending(conn, pending_id)
    if not row:
        answer_callback(callback["id"], "Draft expired or not found.")
        return
    if POST_REPLIES_ENABLED:
        answer_callback(callback["id"], "UiTM posting is not implemented in this phase.")
        return
    update_pending_state(conn, pending_id, "saved")
    answer_callback(callback["id"], "Draft saved.")
    send_message(
        callback["message"]["chat"]["id"],
        f"Draft saved for <b>{html_text(row['topic_title'])}</b>. No UiTM post was made.",
        reply_to_message_id=callback["message"].get("message_id"),
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


def handle_callback(conn, callback):
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if not is_allowed_chat(chat_id):
        answer_callback(callback["id"], "Unauthorized chat.")
        print("Ignored callback from unauthorized chat")
        return
    data = callback.get("data") or ""
    if ":" not in data:
        answer_callback(callback["id"], "Invalid action.")
        return
    prefix, identifier = data.split(":", 1)
    if prefix == "d":
        handle_details(conn, callback, identifier)
    elif prefix == "r":
        handle_reply_start(conn, callback, identifier)
    elif prefix == "s":
        handle_save_draft(conn, callback, identifier)
    elif prefix == "c":
        handle_cancel(conn, callback, identifier)
    else:
        answer_callback(callback["id"], "Unknown action.")


def handle_message(conn, message):
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (message.get("from") or {}).get("id")
    if not is_allowed_chat(chat_id):
        print("Ignored message from unauthorized chat")
        return
    text = (message.get("text") or "").strip()
    if not text:
        return
    if text.lower() == "/cancel":
        pending = latest_open_reply(conn, chat_id, user_id)
        if pending:
            update_pending_state(conn, pending["id"], "cancelled")
            send_message(chat_id, "Reply draft cancelled.", reply_to_message_id=message.get("message_id"))
        return
    pending = latest_pending_reply(conn, chat_id, user_id)
    if not pending:
        return
    update_pending_state(conn, pending["id"], "preview", text)
    keyboard = {
        "inline_keyboard": [[
            {"text": "Save Draft", "callback_data": f"s:{pending['id']}"},
            {"text": "Cancel", "callback_data": f"c:{pending['id']}"},
        ]]
    }
    send_message(
        chat_id,
        "\n".join([
            f"Preview reply to <b>{html_text(pending['topic_title'])}</b>:",
            "",
            html_text(text),
            "",
            "This will only save a draft. UiTM posting is disabled in this phase.",
        ]),
        reply_markup=keyboard,
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
        poll_updates(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
