#!/usr/bin/env python3
"""
Chipotle code watcher

Two modes:
  python watch.py           – continuous loop (Render.com / local)
  python watch.py --once    – single check then exit (GitHub Actions fallback)

Scraping: nitter RSS feeds first (fast, lightweight), then HTML fallback.
Notifications: ntfy.sh push + optional email.
"""

import argparse
import json
import os
import re
import smtplib
import threading
import time
import logging
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from http.server import HTTPServer, BaseHTTPRequestHandler
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import urlopen, Request

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NTFY_TOPIC     = os.getenv("NTFY_TOPIC", "")
SHORTCUT_NAME  = os.getenv("SHORTCUT_NAME", "")

SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER", "")
SMTP_PASS      = os.getenv("SMTP_PASS", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL", "")

HTTP_PORT      = int(os.getenv("HTTP_PORT", "8080"))
# 30 seconds default – fast enough to catch a code within ~1 min of posting
POLL_SECONDS   = int(os.getenv("POLL_SECONDS", "30"))

STATE_FILE     = "seen_codes.json"

# ---------------------------------------------------------------------------
# Nitter instances
# ---------------------------------------------------------------------------
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://xcancel.com",
    "https://nitter.net",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]
CHIPOTLE_HANDLE = "ChipotleTweets"

# ---------------------------------------------------------------------------
# Code detection
# ---------------------------------------------------------------------------
RAW_CODE_RE = re.compile(r'\b([A-Z][A-Z0-9]{3,19})\b')
CONTEXT_WORDS = re.compile(
    r'\b(code|promo|enter|use|redeem|free|bowl|burrito|discount|off|bogo)\b',
    re.IGNORECASE,
)
STOPWORDS = {
    "RETWEET", "FOLLOW", "TWITTER", "CHIPOTLE", "CHIPOTLETWEETS",
    "TODAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
    "SATURDAY", "SUNDAY", "JANUARY", "FEBRUARY", "MARCH", "APRIL",
    "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER",
    "DECEMBER", "THANK", "THANKS", "HAPPY", "ENJOY", "LIMITED",
    "OFFER", "LINK", "CLICK", "HERE", "MORE", "INFO", "TERMS",
    "CONDITIONS", "VALID", "ONLY", "WHILE", "SUPPLIES", "LAST",
    "HTTPS", "HTTP", "WITH", "YOUR", "WILL", "HAVE", "THIS",
    "THAT", "FROM", "THEY", "BEEN", "THEIR", "THERE", "WERE",
}


def extract_codes(text: str) -> list[str]:
    candidates = RAW_CODE_RE.findall(text)
    codes = []
    for c in candidates:
        if c in STOPWORDS:
            continue
        window = text[max(0, text.find(c) - 80): text.find(c) + len(c) + 80]
        if CONTEXT_WORDS.search(window) or f'"{c}"' in text or f"'{c}'" in text:
            codes.append(c)
    return list(dict.fromkeys(codes))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, timeout: int = 10) -> str | None:
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.debug("GET %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Scrapers – RSS first (fast/light), HTML fallback
# ---------------------------------------------------------------------------
def fetch_tweets_rss() -> list[str]:
    """Parse nitter RSS feeds – much faster and lighter than full HTML scraping."""
    for instance in NITTER_INSTANCES:
        url = f"{instance}/{CHIPOTLE_HANDLE}/rss"
        log.debug("Trying RSS: %s", url)
        xml = _get(url, timeout=8)
        if not xml:
            continue
        try:
            root = ET.fromstring(xml)
            # RSS items are under channel/item; text is in <title> or <description>
            ns = {"media": "http://search.yahoo.com/mrss/"}
            items = root.findall(".//item")
            if not items:
                continue
            texts = []
            for item in items[:20]:
                title = item.findtext("title") or ""
                desc = item.findtext("description") or ""
                # Strip HTML tags from description
                clean = re.sub(r"<[^>]+>", " ", desc)
                combined = f"{title} {clean}".strip()
                if combined:
                    texts.append(combined)
            if texts:
                log.info("RSS: got %d items from %s", len(texts), instance)
                return texts
        except ET.ParseError as e:
            log.debug("RSS parse error from %s: %s", instance, e)
    return []


class TweetStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_tweet = 0
        self.texts = []
        self._current = []

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        if "tweet-content" in cls or "tweet-text" in cls:
            self.in_tweet += 1

    def handle_endtag(self, tag):
        if self.in_tweet and tag in ("div", "p"):
            self.in_tweet = max(0, self.in_tweet - 1)
            if self._current:
                self.texts.append(" ".join(self._current).strip())
                self._current = []

    def handle_data(self, data):
        if self.in_tweet:
            self._current.append(data.strip())


def fetch_tweets_html() -> list[str]:
    for instance in NITTER_INSTANCES:
        url = f"{instance}/{CHIPOTLE_HANDLE}"
        html = _get(url)
        if not html:
            continue
        parser = TweetStripper()
        parser.feed(html)
        if parser.texts:
            log.info("HTML: got %d tweets from %s", len(parser.texts), instance)
            return parser.texts[:20]
        raw_text = re.sub(r"<[^>]+>", " ", html)
        if CHIPOTLE_HANDLE.lower() in raw_text.lower():
            return [raw_text[:4000]]
    return []


def fetch_tweets_syndication() -> list[str]:
    url = (
        "https://cdn.syndication.twimg.com/timeline/profile"
        f"?screen_name={CHIPOTLE_HANDLE}&count=20"
    )
    data = _get(url)
    if not data:
        return []
    try:
        obj = json.loads(data)
        html = obj.get("body", "")
        if html:
            parser = TweetStripper()
            parser.feed(html)
            if parser.texts:
                return parser.texts[:20]
        return [re.sub(r"\\n|\\r", " ", data)[:4000]]
    except json.JSONDecodeError:
        return []


def get_all_tweet_texts() -> list[str]:
    # RSS is fastest – try it first
    texts = fetch_tweets_rss()
    if not texts:
        log.info("RSS failed, trying HTML scrape…")
        texts = fetch_tweets_html()
    if not texts:
        log.info("HTML failed, trying syndication…")
        texts = fetch_tweets_syndication()
    return texts


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def send_ntfy(code: str, snippet: str) -> bool:
    if not NTFY_TOPIC:
        return False
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    payload = f"CODE: {code}\n\n{snippet[:200]}".encode()
    headers = {
        "Title": f"Chipotle Code: {code}",
        "Priority": "urgent",
        "Tags": "chipotle,tada",
        "Content-Type": "text/plain",
    }
    if SHORTCUT_NAME:
        shortcut_url = (
            f"shortcuts://run-shortcut"
            f"?name={quote(SHORTCUT_NAME)}"
            f"&input=text"
            f"&text={quote(code)}"
        )
        headers["Actions"] = f"view, Send Code to Shortcut, {shortcut_url}"
    req = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10) as r:
            r.read()
        log.info("ntfy sent: %s", code)
        return True
    except Exception as e:
        log.error("ntfy failed: %s", e)
        return False


def send_email(code: str, snippet: str) -> bool:
    if not all([SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return False
    msg = MIMEText(
        f"Promo code from @ChipotleTweets:\n\n  CODE: {code}\n\n{snippet}\n\n-- Chipotle Watcher"
    )
    msg["Subject"] = f"Chipotle Code Found: {code}"
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [NOTIFY_EMAIL], msg.as_string())
        log.info("Email sent: %s", code)
        return True
    except Exception as e:
        log.error("Email failed: %s", e)
        return False


def notify(code: str, snippet: str):
    send_ntfy(code, snippet)
    send_email(code, snippet)


# ---------------------------------------------------------------------------
# State persistence (--once mode)
# ---------------------------------------------------------------------------
def load_seen() -> set[str]:
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set[str]):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(seen), f)


# ---------------------------------------------------------------------------
# Local HTTP server (continuous mode)
# ---------------------------------------------------------------------------
latest_code: dict = {"code": "", "context": "", "found_at": ""}
latest_lock = threading.Lock()


class CodeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with latest_lock:
            code = latest_code["code"]
        if self.path.rstrip("/") == "/code":
            body = (code or "NO_CODE_YET").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.rstrip("/") == "/status":
            with latest_lock:
                payload = json.dumps(latest_code).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *_):
        pass


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def run_once():
    """Single check – for GitHub Actions / cron (slower, ~5 min cadence)."""
    seen = load_seen()
    tweets = get_all_tweet_texts()
    if not tweets:
        log.warning("No tweets retrieved")
        return
    found_any = False
    for tweet in tweets:
        for code in extract_codes(tweet):
            if code not in seen:
                seen.add(code)
                snippet = tweet[:200].replace("\n", " ")
                log.info("NEW CODE: %s | %s", code, snippet)
                notify(code, snippet)
                found_any = True
    save_seen(seen)
    if not found_any:
        log.info("No new codes this run.")


def run_loop():
    """
    Continuous loop – runs on Render.com / local.
    Polls every POLL_SECONDS (default 30) for near-instant detection.
    """
    seen: set[str] = set()

    # HTTP server for local Apple Shortcuts polling
    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", HTTP_PORT), CodeHandler).serve_forever(),
        daemon=True,
    ).start()
    log.info("HTTP server → http://localhost:%d/code", HTTP_PORT)

    log.info(
        "Polling @%s every %ds | ntfy=%s",
        CHIPOTLE_HANDLE, POLL_SECONDS, NTFY_TOPIC or "off",
    )

    while True:
        try:
            tweets = get_all_tweet_texts()
            for tweet in tweets:
                for code in extract_codes(tweet):
                    if code not in seen:
                        seen.add(code)
                        snippet = tweet[:200].replace("\n", " ")
                        log.info("NEW CODE: %s | %s", code, snippet)
                        with latest_lock:
                            latest_code.update(
                                code=code, context=snippet,
                                found_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                            )
                        notify(code, snippet)
        except Exception as e:
            log.error("Poll error: %s", e)

        log.info("Next poll in %ds…", POLL_SECONDS)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true",
        help="Single check then exit (GitHub Actions fallback, ~5 min cadence)",
    )
    args = parser.parse_args()
    if args.once:
        run_once()
    else:
        run_loop()
