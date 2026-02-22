# Chipotle Code Watcher

Polls @ChipotleTweets every 5 minutes, pulls out promo codes, emails them to you,
and serves the latest code at `http://localhost:8080/code` for Apple Shortcuts.

## Quick start (Mac)

```bash
# 1. Install deps (Python 3.11+ required)
pip install -r requirements.txt

# 2. Copy and fill in credentials
cp .env.example .env
open -e .env      # or nano .env

# 3. Run it
python watch.py
```

The terminal will log every poll. Leave it open tonight.

---

## Gmail App Password (30 seconds)

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Click **Create** → name it "Chipotle Bot"
3. Copy the 16-char password → paste into `SMTP_PASS` in `.env`

---

## Apple Shortcuts setup

### Option A – Email trigger (simplest)
The bot emails you when it finds a code. Set up a Shortcut automation that
triggers on **mail from yourself** with "Chipotle Code Found" in the subject.

### Option B – Poll the local HTTP server
Add a Shortcut that runs on a schedule (every 5 min):

1. **Get Contents of URL** → `http://localhost:8080/code`
   *(Mac must be on the same network; use your Mac's local IP if running from iPhone)*
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
