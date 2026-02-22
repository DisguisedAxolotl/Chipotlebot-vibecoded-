# Chipotle Code Watcher

Monitors @ChipotleTweets on X and pushes promo codes to your iPhone
**within ~30 seconds** of them being posted.

---

## How it works

```
X / nitter RSS feed (polled every 30s)
        ↓ new code found
  ntfy.sh push notification
        ↓ tap "Send Code to Shortcut"
  Apple Shortcut runs with code as input
        ↓
  iMessage sent to yourself (or clipboard, or whatever)
```

---

## Fastest setup: Render.com (free, no server, ~30s detection)

**1. Deploy**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

Or manually:
1. Go to [render.com](https://render.com) → sign up free (no credit card)
2. New → **Background Worker** → connect this GitHub repo
3. Render auto-detects `render.yaml` and configures everything

**2. Set environment variables** in the Render dashboard:

| Variable | Value |
|---|---|
| `NTFY_TOPIC` | your secret topic name, e.g. `chipotle-bowl-abc987` |
| `SHORTCUT_NAME` | name of your Apple Shortcut (optional) |
| `POLL_SECONDS` | `30` (already set in render.yaml) |

**3. Deploy** → it starts polling immediately, runs forever, free.

---

## Alternative: GitHub Actions (free, ~5 min detection)

Slower than Render (GitHub minimum cron is 5 minutes) but zero setup.

Add these as **repository secrets** (Settings → Secrets → Actions):
- `NTFY_TOPIC`
- `SHORTCUT_NAME` (optional)

The workflow (`.github/workflows/chipotle-watcher.yml`) runs automatically.
Enable it under the **Actions** tab if needed.

---

## Notification setup

### ntfy.sh push (recommended, no account needed)

1. Install the free **ntfy** app on iPhone (App Store)
2. In the ntfy app, tap **+** → subscribe to your topic name
3. Set `NTFY_TOPIC` to the same name in your deployment

#### Shortcut deep-link (one tap → code auto-sent)

1. Shortcuts app → **+** → name it e.g. `Chipotle Code`
2. Add: **Receive input from** → Quick Actions
3. Add: **Send Message** → body = *Shortcut Input* → recipient = yourself
4. Set `SHORTCUT_NAME=Chipotle Code` in your deployment

When the notification arrives, tap **"Send Code to Shortcut"** → code is
instantly texted to you. No copy-paste.

---

## Local run (Mac)

```bash
pip install -r requirements.txt
cp env.example .env
# edit .env
python watch.py           # continuous, polls every 30s
python watch.py --once    # single check then exit
```

---

## How codes are detected

- Fetches nitter **RSS feeds** first (fast, lightweight XML)
- Falls back to nitter HTML scraping, then X syndication endpoint
- Extracts ALL-CAPS strings 4–20 chars long that appear near words
  like "code", "promo", "enter", "redeem", "free", "bowl", etc.
