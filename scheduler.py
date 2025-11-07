import schedule
import time
import subprocess
import datetime
import sys

def run_scraper():
    try:
        print(f"[{datetime.datetime.now()}] Running scraper...")
        result = subprocess.run(['python', '/app/ufuture-scraper.py'], capture_output=True, text=True, timeout=300)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        print(f"[{datetime.datetime.now()}] Scraper completed with code {result.returncode}")
    except Exception as e:
        print(f"[{datetime.datetime.now()}] ERROR running scraper: {e}")

schedule.every(10).minutes.do(run_scraper)

print("Scheduler started. Running scraper every 10 minutes")
sys.stdout.flush()

try:
    while True:
        schedule.run_pending()
        time.sleep(60)
except KeyboardInterrupt:
    print("Scheduler stopped by user")
except Exception as e:
    print(f"Scheduler error: {e}")
    time.sleep(60)
