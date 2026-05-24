import schedule
import time
import subprocess
import datetime
import sys
import os

SCRAPER_PATH = os.getenv("SCRAPER_PATH", "/app/ufuture-scraper.py")
SCRAPER_TIMEOUT_SECONDS = int(os.getenv("SCRAPER_TIMEOUT_SECONDS", "300"))
SCRAPER_FAILURE_LIMIT = int(os.getenv("SCRAPER_FAILURE_LIMIT", "0"))
consecutive_failures = 0

def run_scraper():
    global consecutive_failures

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
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            print(f"[{datetime.datetime.now()}] Consecutive scraper failures: {consecutive_failures}")
            if SCRAPER_FAILURE_LIMIT and consecutive_failures >= SCRAPER_FAILURE_LIMIT:
                print(
                    f"[{datetime.datetime.now()}] Failure limit reached "
                    f"({SCRAPER_FAILURE_LIMIT}); exiting scheduler"
                )
                sys.exit(result.returncode)
        sys.stdout.flush()
    except subprocess.TimeoutExpired:
        consecutive_failures += 1
        print(f"[{datetime.datetime.now()}] ERROR scraper timed out after {SCRAPER_TIMEOUT_SECONDS}s")
        sys.stdout.flush()
        if SCRAPER_FAILURE_LIMIT and consecutive_failures >= SCRAPER_FAILURE_LIMIT:
            sys.exit(1)
    except Exception as e:
        consecutive_failures += 1
        print(f"[{datetime.datetime.now()}] ERROR running scraper: {e}")
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
