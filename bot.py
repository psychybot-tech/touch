import os
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Load config from .env
# ─────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", 30))
NITTER_INSTANCES = [
    h.strip()
    for h in os.getenv(
        "NITTER_INSTANCES",
        "nitter.net,nitter.privacydev.net,nitter.poast.org",
    ).split(",")
    if h.strip()
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TweetBot/1.0)",
}


# ─────────────────────────────────────────────
# RSS fetcher using stdlib urllib (no feedparser needed)
# ─────────────────────────────────────────────
def fetch_rss_sync(username: str):
    """Try each Nitter instance in order. Returns list of (tweet_id, text, url) tuples."""
    for host in NITTER_INSTANCES:
        url = f"https://{host}/{username}/rss"
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()

            root = ET.fromstring(raw)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item")

            if not items:
                log.warning("%s: empty feed, trying next.", host)
                continue

            results = []
            for item in items:
                link_el = item.find("link")
                desc_el = item.find("description")
                title_el = item.find("title")

                if link_el is None:
                    continue

                link = link_el.text.strip() if link_el.text else ""
                tweet_id = link.rstrip("/").split("/")[-1]

                # Prefer description (full tweet text), fall back to title
                raw_text = ""
                if desc_el is not None and desc_el.text:
                    raw_text = desc_el.text
                elif title_el is not None and title_el.text:
                    raw_text = title_el.text

                # Strip HTML tags
                text = re.sub(r"<[^>]+>", "", raw_text).strip()
                # Decode common HTML entities
                text = (text.replace("&amp;", "&")
                            .replace("&lt;", "<")
                            .replace("&gt;", ">")
                            .replace("&quot;", '"')
                            .replace("&#39;", "'"))

                tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"
                results.append((tweet_id, text, tweet_url))

            log.debug("Got %d items from %s", len(results), host)
            return results

        except ET.ParseError as e:
            log.warning("%s: XML parse error: %s, trying next.", host, e)
        except urllib.error.URLError as e:
            log.warning("%s: URL error: %s, trying next.", host, e)
        except Exception as e:
            log.warning("%s: unexpected error: %s, trying next.", host, e)

    return None


async def fetch_rss(username: str):
    """Run the blocking RSS fetch in a thread pool so the event loop stays free."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_rss_sync, username)


# ─────────────────────────────────────────────
# Main polling loop
# ─────────────────────────────────────────────
async def main():
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TWITTER_USERNAME]):
        log.error(
            "Missing required environment variables. "
            "Check TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TWITTER_USERNAME in your .env."
        )
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    seen_ids: set[str] = set()
    initialized = False

    log.info(
        "🚀 Starting bot — monitoring @%s, polling every %ds via Nitter RSS",
        TWITTER_USERNAME,
        POLL_INTERVAL,
    )

    while True:
        try:
            entries = await fetch_rss(TWITTER_USERNAME)

            if entries is None:
                log.error("All Nitter instances failed. Retrying in %ds.", POLL_INTERVAL)
            elif not entries:
                log.info("Feed is empty.")
            else:
                if not initialized:
                    # Seed existing tweets on first run — don't spam old content
                    for tweet_id, _, _ in entries:
                        seen_ids.add(tweet_id)
                    log.info(
                        "✅ Initialized. Seeded %d existing tweets. Watching for new ones…",
                        len(seen_ids),
                    )
                    initialized = True
                else:
                    # Entries come newest-first; reverse so we post oldest-new first
                    new_entries = [
                        (tid, text, url)
                        for tid, text, url in reversed(entries)
                        if tid not in seen_ids
                    ]

                    for tweet_id, text, tweet_url in new_entries:
                        message = f"🐦 @{TWITTER_USERNAME}\n\n{text}\n\n{tweet_url}"
                        try:
                            await bot.send_message(
                                chat_id=TELEGRAM_CHANNEL_ID,
                                text=message,
                            )
                            seen_ids.add(tweet_id)
                            log.info("📨 Posted tweet %s to Telegram.", tweet_id)
                        except TelegramError as te:
                            log.error("Telegram error for tweet %s: %s", tweet_id, te)

        except Exception as e:
            log.exception("Unexpected error in polling loop: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
