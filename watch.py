#!/usr/bin/env python3
"""
Chipotle code watcher
Polls @ChipotleTweets on X every few minutes, extracts promo codes,
sends an email, and serves the latest code on http://localhost:8080/code
so Apple Shortcuts can GET it with "Get Contents of URL".
"""

import os
import re
import smtplib
import time
import json
import threading
import logging
from email.mime.text import MIMEText
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.error import URLError
from html.parser import HTMLParser

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (all from .env)
# ---------------------------------------------------------------------------
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")          # your Gmail address
SMTP_PASS     = os.getenv("SMTP_PASS", "")          # Gmail App Password
NOTIFY_EMAIL  = os.getenv("NOTIFY_EMAIL", "")       # where to send the code
HTTP_PORT     = int(os.getenv("HTTP_PORT", "8080"))
POLL_SECONDS  = int(os.getenv("POLL_SECONDS", "300"))  # default: 5 min

# ---------------------------------------------------------------------------
# Nitter instances to try (in order)
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
# Chipotle codes are typically ALL-CAPS, 4–20 chars, alphanumeric.
# We tighten the match by requiring they appear near code-related words
# OR are standalone quoted/highlighted in the tweet.
RAW_CODE_RE = re.compile(r'\b([A-Z][A-Z0-9]{3,19})\b')
CONTEXT_WORDS = re.compile(
    r'\b(code|promo|enter|use|redeem|free|bowl|burrito|discount|off|bogo)\b',
    re.IGNORECASE,
)
# These common English words are NOT codes – skip them
STOPWORDS = {
    "RETWEET", "FOLLOW", "TWITTER", "CHIPOTLE", "CHIPOTLETWEETS",
    "TODAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
    "SATURDAY", "SUNDAY", "JANUARY", "FEBRUARY", "MARCH", "APRIL",
    "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER",
    "DECEMBER", "THANK", "THANKS", "HAPPY", "ENJOY", "LIMITED",
    "OFFER", "LINK", "CLICK", "HERE", "MORE", "INFO", "TERMS",
    "CONDITIONS", "VALID", "ONLY", "WHILE", "SUPPLIES", "LAST",
}

def extract_codes(text: str) -> list[str]:
    """Return candidate promo codes from a tweet body."""
    candidates = RAW_CODE_RE.findall(text)
    codes = []
    for c in candidates:
        if c in STOPWORDS:
            continue
        if len(c) < 4:
            continue
        # Require a nearby context word OR the code looks quote-surrounded
        window = text[max(0, text.find(c) - 80): text.find(c) + len(c) + 80]
        if CONTEXT_WORDS.search(window) or f'"{c}"' in text or f"'{c}'" in text:
            codes.append(c)
    return list(dict.fromkeys(codes))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class TweetStripper(HTMLParser):
    """Minimal HTML parser that collects visible text from tweet divs."""
    def __init__(self):
        super().__init__()
        self.in_tweet = 0
        self.texts = []
        self._current = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        cls = attr_dict.get("class", "")
        if "tweet-content" in cls or "tweet-text" in cls or "tgme_widget_message_text" in cls:
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


def _get(url: str, timeout: int = 10) -> str | None:
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.debug("GET %s failed: %s", url, e)
        return None


def fetch_tweets_nitter() -> list[str]:
    for instance in NITTER_INSTANCES:
        url = f"{instance}/{CHIPOTLE_HANDLE}"
        log.info("Trying nitter: %s", url)
        html = _get(url)
        if not html:
            continue
        parser = TweetStripper()
        parser.feed(html)
        if parser.texts:
            log.info("Got %d tweets from %s", len(parser.texts), instance)
            return parser.texts[:20]
        # Fallback: grab all visible text and look for code patterns anyway
        raw_text = re.sub(r"<[^>]+>", " ", html)
        if CHIPOTLE_HANDLE.lower() in raw_text.lower():
            log.info("Partial parse from %s", instance)
            return [raw_text[:4000]]
    return []


def fetch_tweets_syndication() -> list[str]:
    """
    X's public syndication endpoint – works without an API key for
    recent timeline embeds. Hit-or-miss depending on X's changes.
    """
    url = (
        "https://cdn.syndication.twimg.com/timeline/profile"
        f"?screen_name={CHIPOTLE_HANDLE}&count=20"
    )
    log.info("Trying X syndication: %s", url)
    data = _get(url)
    if not data:
        return []
    try:
        obj = json.loads(data)
        # The body field contains rendered HTML
        html = obj.get("body", "")
        if html:
            parser = TweetStripper()
            parser.feed(html)
            if parser.texts:
                return parser.texts[:20]
        # Fallback: regex on raw JSON
        raw = re.sub(r"\\n|\\r", " ", data)
        return [raw[:4000]]
    except json.JSONDecodeError:
        return []


def get_all_tweet_texts() -> list[str]:
    texts = fetch_tweets_nitter()
    if not texts:
        texts = fetch_tweets_syndication()
    return texts


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def send_email(code: str, tweet_snippet: str) -> bool:
    if not all([SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        log.warning("Email not configured – skipping send (code: %s)", code)
        return False
    subject = f"Chipotle Code Found: {code}"
    body = (
        f"Promo code detected from @ChipotleTweets:\n\n"
        f"  CODE: {code}\n\n"
        f"Tweet context:\n{tweet_snippet}\n\n"
        f"-- Chipotle Watcher"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [NOTIFY_EMAIL], msg.as_string())
        log.info("Email sent for code: %s", code)
        return True
    except Exception as e:
        log.error("Email failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# HTTP server – Apple Shortcuts polls http://localhost:8080/code
# ---------------------------------------------------------------------------
latest_code: dict = {"code": "", "context": "", "found_at": ""}
latest_lock = threading.Lock()


class CodeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with latest_lock:
            code = latest_code["code"]
            context = latest_code["context"]
            found_at = latest_code["found_at"]
        if self.path in ("/code", "/code/"):
            body = (code or "NO_CODE_YET").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/status", "/status/"):
            payload = json.dumps({
                "code": code,
                "context": context,
                "found_at": found_at,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence default httpd logs
        pass


def start_http_server():
    srv = HTTPServer(("0.0.0.0", HTTP_PORT), CodeHandler)
    log.info("HTTP server on http://localhost:%d/code", HTTP_PORT)
    srv.serve_forever()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    seen_codes: set[str] = set()

    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    log.info(
        "Watcher started. Polling @%s every %ds. Email → %s",
        CHIPOTLE_HANDLE, POLL_SECONDS, NOTIFY_EMAIL or "(not configured)",
    )

    while True:
        try:
            tweets = get_all_tweet_texts()
            if not tweets:
                log.warning("No tweets retrieved this round")
            for tweet in tweets:
                codes = extract_codes(tweet)
                for code in codes:
                    if code not in seen_codes:
                        seen_codes.add(code)
                        snippet = tweet[:200].replace("\n", " ")
                        log.info("NEW CODE FOUND: %s  |  %s", code, snippet)
                        with latest_lock:
                            latest_code["code"] = code
                            latest_code["context"] = snippet
                            latest_code["found_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        send_email(code, snippet)
        except Exception as e:
            log.error("Poll error: %s", e)

        log.info("Sleeping %ds until next poll…", POLL_SECONDS)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
