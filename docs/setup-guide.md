# Setup guide (no technical experience needed)

This guide gets uFuture Telegram Notifier running on your own computer in about 20 minutes.
You'll get your UiTM uFuture forum updates, lecturers' files and replies in Telegram.

## Before you start: what you need

| You need | Why |
|---|---|
| **A computer that stays on** (Windows 10/11 or a Mac) | The app checks uFuture every 10 minutes. It only works while that computer is **on and awake**. A desktop PC, a home server, a mini-PC or a Raspberry Pi is ideal; a laptop works only while it's open and awake. |
| **Telegram** on your phone | That's where the updates arrive. |
| **Your uFuture login** | Your student ID and password. They stay on your computer. |
| **About 20 minutes** | Mostly waiting for installs. |

Everyone runs **their own copy**: your login and your bot are only for you.

---

## Step 1: Install Docker Desktop (one time)

Docker is a free program that runs the app for you.

1. Go to **https://www.docker.com/products/docker-desktop/** and download Docker Desktop for your computer:
   - **Windows:** "Download for Windows".
   - **Mac:** pick **Apple Silicon** (M1/M2/M3/M4 chips) or **Intel**. Not sure? Apple menu → About This Mac → "Chip".
2. Open the downloaded file and install it like any program. On Windows, accept "Use WSL 2" if asked and restart when told.
3. Open **Docker Desktop**. Accept the terms. You can skip signing in.
4. Wait until the bottom-left corner shows **"Engine running"** (green).
5. Recommended: in Docker Desktop, open **Settings (⚙) → General** and turn on **"Start Docker Desktop when you sign in to your computer"**, so the app comes back after a restart.

## Step 2: Create your Telegram bot (one time, 2 minutes)

1. In Telegram, search for **@BotFather** (blue tick) and open it.
2. Send **`/newbot`**.
3. Send a **name**, for example `My uFuture`.
4. Send a **username** ending in `bot`, for example `ali_ufuture_bot`.
5. BotFather replies with a **token** like `1234567890:AAH-xYzAbCdEf...`. **Copy it**; you'll need it in Step 4. Keep it private.
6. Tap the link to your new bot (`t.me/ali_ufuture_bot`) and press **START**. *(Important: a bot can't message you until you press START.)*

## Step 3: Find your Telegram ID (one time, 1 minute)

1. In Telegram, search for **@userinfobot** and press **START**.
2. It replies with your **Id**, a number like `123456789`. **Copy it.**

## Step 4: Download the app files and fill in your settings

1. Make a new folder, for example **`uFuture`** in your **Documents**.
2. Download the setup files from the latest release:
   **Releases → latest version → Assets → `ufuture-setup.zip`**.
   Unzip it into your `uFuture` folder. You should now have:
   - `docker-compose.yml`
   - `.env.example`
   - `setup-guide.md` (this guide)

   > **Mac:** files starting with a dot are hidden. In Finder, press **⌘ Shift .** (Command + Shift + full stop) to show them.
   > **Windows:** in File Explorer, open **View → Show → File name extensions** so you can see the real file names.

3. **Copy** `.env.example` and name the copy exactly **`.env`**.
   - **Windows:** right-click → Copy, Paste, then rename the copy to `.env`. If Windows warns about changing the extension, click **Yes**. The name must be `.env`, **not** `.env.txt` or `.env.example`.
   - **Mac:** select the file, press **⌘ D** (duplicate), then rename the copy to `.env`. If asked, click **Use "."**.
4. Open **`.env`** in a text editor:
   - **Windows:** right-click → **Open with → Notepad**.
   - **Mac:** right-click → **Open With → TextEdit**. If you see formatting buttons, choose **Format → Make Plain Text**.
5. Fill in **PART 1** (5 values). The notes in the file explain each one:

   | Setting | What to write |
   |---|---|
   | `UFUTURE_USERNAME` | Your student ID |
   | `UFUTURE_PASSWORD` | Your uFuture password, **between the single quotes**: `UFUTURE_PASSWORD='yourPassword'` |
   | `TELEGRAM_BOT_TOKEN` | The token from Step 2 |
   | `TELEGRAM_CHAT_ID` | Your Id from Step 3 |
   | `TELEGRAM_ALLOWED_USER_IDS` | The same Id from Step 3 |

   Example (with made-up values):
   ```
   UFUTURE_USERNAME=2023123456
   UFUTURE_PASSWORD='MySecret#2026'
   TELEGRAM_BOT_TOKEN=1234567890:AAH-xYzAbCdEfGhIjKlMnOpQrStUvWxYz12
   TELEGRAM_CHAT_ID=123456789
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```
   No spaces around `=`. Leave PART 2 and PART 3 as they are unless you want to change something.
6. **Save** and close the file.

## Step 5: Start the app

1. Open a terminal **inside your `uFuture` folder**:
   - **Windows:** open the folder in File Explorer, **right-click an empty space → "Open in Terminal"**.
   - **Mac:** open **Terminal** (⌘ Space, type "Terminal"), type `cd ` (with a space after it), **drag the `uFuture` folder into the Terminal window**, and press **Enter**.
2. Type this and press **Enter**:
   ```
   docker compose up -d
   ```
   The first time, it downloads the app (a minute or two). When you see `Started`, it's running.
3. Check that it works:
   ```
   docker compose logs -f
   ```
   Within a minute you should see **`Logged in successfully`** and **`Completed`**. Press **Ctrl + C** to stop watching (the app keeps running).
4. In Telegram, send your bot **`/status`**. It should reply with your courses and when it last checked.

**The first check sends no notifications.** That's normal: it saves the current state of your forums, and from now on you're told about **new** posts.

That's it. You can close the terminal. Docker keeps the app running in the background.

---

## Using it

- **New lecturer posts, new topics and replies to your posts** arrive with sound; **classmates' replies** come as one silent summary.
- Tap **`▸`** on a message for details, files and replying.
- Commands: `/latest` (newest posts), `/courses` (browse), `/files` (get course files), `/status`, `/mute CSC649`, `/help`.
- **Replying from Telegram** is off by default. To turn it on, set `UFUTURE_REPLY_ENABLED=true` in `.env`, then run `docker compose up -d` again. Replies are posted under **your name** on uFuture, and you always see a preview first.

## Everyday tasks

Open a terminal in your `uFuture` folder (Step 5.1), then:

| To… | Type |
|---|---|
| **Update** to the newest version | `docker compose pull` then `docker compose up -d` |
| **Apply changed settings** (after editing `.env`) | `docker compose up -d` |
| **See what it's doing** | `docker compose logs -f` (Ctrl + C to stop watching) |
| **Stop** it | `docker compose down` |
| **Start** it again | `docker compose up -d` |

## Something's wrong?

| What you see | What to do |
|---|---|
| `no configuration file provided` | The terminal isn't in your `uFuture` folder. Redo Step 5.1. |
| `env file … .env not found` | The settings file isn't named exactly `.env`. Check Step 4.3 (no `.txt`, no `.example`). |
| `Cannot connect to the Docker daemon` / `docker: command not found` | Docker Desktop isn't running. Open it and wait for **"Engine running"**. |
| Logs say **`UiTM login was rejected`** | Wrong student ID or password in `.env`. Fix it, save, run `docker compose up -d`. (It doesn't keep retrying, so your account won't be locked.) |
| Logs say **`… not set`** | One of the PART 1 values is empty. Fill it in. |
| Logs say **`DNS lookup failed`** | Your internet or network is down, or a network filter blocks `ufuture.uitm.edu.my`. It retries automatically. |
| The bot never messages you | Press **START** in your bot's chat (Step 2.6), and check `TELEGRAM_CHAT_ID` is **your** Id from @userinfobot. |
| Bot says **"Unauthorized user"** | `TELEGRAM_ALLOWED_USER_IDS` doesn't contain your Id. |
| Updates stop when your computer sleeps | That's expected. Keep the computer awake (Windows: Settings → System → Power → Sleep = Never; Mac: System Settings → Energy/Battery → prevent sleeping) or run it on an always-on machine. |

## Privacy and safety

- Your password and tokens are only in your `.env` file, on your computer. **Never share `.env`** or post it in a group chat.
- The app only reads **your own** courses, as you would in the browser, and checks gently (every 10 minutes).
- Downloaded files go straight to your Telegram; nothing is stored on your computer except the app's small database in the `data` folder.
- Use it responsibly and within UiTM's rules.
