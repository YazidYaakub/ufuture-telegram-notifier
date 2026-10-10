import schedule
import time
import subprocess
import datetime
import sys
import os
import requests

SCRAPER_PATH = os.getenv("SCRAPER_PATH", "/app/ufuture-scraper.py")
SCRAPER_TIMEOUT_SECONDS = int(os.getenv("SCRAPER_TIMEOUT_SECONDS", "300"))
SCRAPER_FAILURE_LIMIT = int(os.getenv("SCRAPER_FAILURE_LIMIT", "0"))
SCRAPER_ALERT_AFTER = int(os.getenv("SCRAPER_ALERT_AFTER", "3"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}
consecutive_failures = 0
failure_alert_sent = False

FAILURE_REASONS = {
    2: "missing configuration",
    3: "UiTM login rejected (check username/password)",
    75: "network or DNS problem",
}


def failure_reason(returncode):
    if returncode == "timeout":
        return f"scraper timed out after {SCRAPER_TIMEOUT_SECONDS}s"
    return FAILURE_REASONS.get(returncode, "scraper error (UiTM page layout may have changed)")


def send_alert(text):
    if DRY_RUN:
        print(f"DRY_RUN alert not sent: {text}")
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"[{datetime.datetime.now()}] Alert send failed: HTTP {response.status_code}")
    except requests.RequestException as error:
        print(f"[{datetime.datetime.now()}] Alert send error: {type(error).__name__}")


def record_success():
    global consecutive_failures, failure_alert_sent
    if failure_alert_sent:
        send_alert(f"✅ Forum scraper recovered after {consecutive_failures} failed run(s).")
    consecutive_failures = 0
    failure_alert_sent = False


def record_failure(returncode):
    global consecutive_failures, failure_alert_sent
    consecutive_failures += 1
    print(f"[{datetime.datetime.now()}] Consecutive scraper failures: {consecutive_failures}")
    if SCRAPER_ALERT_AFTER and consecutive_failures >= SCRAPER_ALERT_AFTER and not failure_alert_sent:
        send_alert(
            f"⚠️ Forum scraper failing: {consecutive_failures} runs in a row.\n"
            f"Reason: {failure_reason(returncode)}\n"
            "No forum updates will arrive until this is fixed."
        )
        failure_alert_sent = True

def run_scraper():
    try:
        print(f"[{datetime.datetime.now()}] Running scraper...")
        sys.stdout.flush()
        result = subprocess.run(
            [sys.executable, SCRAPER_PATH],
            capture_output=True,
            text=True,
            timeout=SCRAPER_TIMEOUT_SECONDS,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        print(f"[{datetime.datetime.now()}] Scraper completed with code {result.returncode}")
        if result.returncode == 0:
            record_success()
        else:
            record_failure(result.returncode)
            if SCRAPER_FAILURE_LIMIT and consecutive_failures >= SCRAPER_FAILURE_LIMIT:
                print(
                    f"[{datetime.datetime.now()}] Failure limit reached "
                    f"({SCRAPER_FAILURE_LIMIT}); exiting scheduler"
                )
                sys.exit(result.returncode)
        sys.stdout.flush()
    except subprocess.TimeoutExpired:
        print(f"[{datetime.datetime.now()}] ERROR scraper timed out after {SCRAPER_TIMEOUT_SECONDS}s")
        record_failure("timeout")
        sys.stdout.flush()
        if SCRAPER_FAILURE_LIMIT and consecutive_failures >= SCRAPER_FAILURE_LIMIT:
            sys.exit(1)
    except Exception as e:
        print(f"[{datetime.datetime.now()}] ERROR running scraper: {e}")
        record_failure(None)
        sys.stdout.flush()
        if SCRAPER_FAILURE_LIMIT and consecutive_failures >= SCRAPER_FAILURE_LIMIT:
            sys.exit(1)

schedule.every(10).minutes.do(run_scraper)

print("Scheduler started. Running scraper every 10 minutes")
sys.stdout.flush()
run_scraper()

try:
    while True:
        schedule.run_pending()
        time.sleep(60)
except KeyboardInterrupt:
    print("Scheduler stopped by user")
except Exception as e:
    print(f"Scheduler error: {e}")
    time.sleep(60)
