# Chipotle Code Watcher

Polls @ChipotleTweets every 5 minutes, pulls out promo codes, emails them to you,
and serves the latest code at `http://localhost:8080/code` for Apple Shortcuts.

## Quick start (Mac)

```bash
# 1. Install deps (Python 3.11+ required)
pip install -r requirements.txt

# 2. Copy and fill in credentials
cp env.example .env
open -e .env      # or: nano .env

# 3. Run it
python watch.py
```

The terminal will log every poll. Leave it open tonight.

---

## Notification setup (pick one)

### Option A – ntfy.sh push notification (EASIEST, recommended)

No account needed. Free. Works great with Apple Shortcuts.

1. Install the free **[ntfy app](https://apps.apple.com/app/ntfy/id1625396347)** on your iPhone
2. In `.env`, set `NTFY_TOPIC` to any secret string, e.g. `chipotle-free-bowl-abc987`
3. In the ntfy app, tap **+** and subscribe to that same topic name
4. Done — you'll get an instant push when a code is found

#### Shortcut deep-link (tap → code auto-sent)

1. In the **Shortcuts app**, create a new Shortcut named e.g. `Chipotle Code`
2. Add these actions:
   - **Receive input from** → Quick Actions, Share Sheet, Shortcuts app
   - **Send Message** → body = `Shortcut Input` → to yourself
     *(or Copy to Clipboard, or whatever you want)*
3. In `.env`, set `SHORTCUT_NAME=Chipotle Code`

Now when the ntfy notification arrives, tap **"Send Code to Shortcut"** and
iOS instantly runs your Shortcut with the code as the input — no copy-paste needed.

### Option B – Poll the local HTTP server

Add a Shortcut that runs on a timer (every 5 min):

1. **Get Contents of URL** → `http://YOUR-MAC-LOCAL-IP:8080/code`
   (find your Mac's IP: System Settings → Wi-Fi → Details)
2. **If** result ≠ `NO_CODE_YET`
3. **Send Message** (or copy to clipboard)

`/status` returns JSON with `{ "code", "context", "found_at" }` if you need more detail.

---

## How codes are detected

The scraper tries, in order:
1. Several public **nitter** mirrors of X (no API key needed)
2. X's **syndication** embed endpoint

It then scans each tweet for ALL-CAPS strings 4–20 characters long that appear
near words like "code", "promo", "enter", "redeem", "free", etc.
