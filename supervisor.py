import os
import signal
import subprocess
import sys
import time


APP_DIR = "/app"
SCHEDULER_PATH = os.getenv("SCHEDULER_PATH", os.path.join(APP_DIR, "scheduler.py"))
INTERACTIVE_BOT_PATH = os.getenv("TELEGRAM_INTERACTIVE_BOT_PATH", os.path.join(APP_DIR, "telegram_interactive_bot.py"))
INTERACTIVE_ENABLED = os.getenv("TELEGRAM_INTERACTIVE_BOT", "true").strip().lower() in {"1", "true", "yes", "on"}
BOT_RESTART_SECONDS = int(os.getenv("TELEGRAM_BOT_RESTART_SECONDS", "5"))
RUNNING = True
CHILDREN = []


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False
    for child in CHILDREN:
        if child and child.poll() is None:
            child.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def start_process(label, path):
    print(f"Starting {label}: {path}")
    sys.stdout.flush()
    return subprocess.Popen([sys.executable, path])


def terminate_process(child, timeout=10):
    if not child or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def main():
    scheduler = start_process("scheduler", SCHEDULER_PATH)
    bot = start_process("telegram interactive bot", INTERACTIVE_BOT_PATH) if INTERACTIVE_ENABLED else None
    CHILDREN[:] = [child for child in (scheduler, bot) if child is not None]

    try:
        while RUNNING:
            scheduler_code = scheduler.poll()
            if scheduler_code is not None:
                print(f"Scheduler exited with code {scheduler_code}; stopping supervisor")
                if bot:
                    terminate_process(bot)
                return scheduler_code

            if bot:
                bot_code = bot.poll()
                if bot_code is not None and RUNNING:
                    print(f"Telegram interactive bot exited with code {bot_code}; restarting in {BOT_RESTART_SECONDS}s")
                    time.sleep(BOT_RESTART_SECONDS)
                    bot = start_process("telegram interactive bot", INTERACTIVE_BOT_PATH)
                    CHILDREN[:] = [scheduler, bot]

            time.sleep(2)
    finally:
        terminate_process(bot)
        terminate_process(scheduler)

    return 0


if __name__ == "__main__":
    sys.exit(main())
