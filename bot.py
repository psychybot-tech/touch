import os
import asyncio
import logging
import re
from html import escape
from dotenv import load_dotenv
from twikit import Client, TooManyRequests
from telegram import Bot, InputMediaPhoto
from telegram.error import TelegramError

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID  = os.getenv("TELEGRAM_CHANNEL_ID")
TWITTER_USERNAME     = os.getenv("TWITTER_USERNAME")      # account to MONITOR
TWITTER_AUTH_EMAIL   = os.getenv("TWITTER_AUTH_EMAIL")    # burner login
TWITTER_AUTH_USER    = os.getenv("TWITTER_AUTH_USERNAME")
TWITTER_AUTH_PASS    = os.getenv("TWITTER_AUTH_PASSWORD")
TWITTER_AUTH_TOKEN   = os.getenv("TWITTER_AUTH_TOKEN")
TWITTER_CT0          = os.getenv("TWITTER_CT0")
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL_SECONDS", 30))

COOKIES_FILE = "cookies.json"

# ─────────────────────────────────────────────
# Twitter client setup
# ─────────────────────────────────────────────
async def get_twitter_client() -> Client:
    client = Client(language="en-US")

    # Method 1: Direct session cookies (Bypasses guest activation / KEY_BYTE errors on cloud servers)
    if TWITTER_AUTH_TOKEN and TWITTER_CT0:
        log.info("Authenticating using provided session cookies (auth_token & ct0)…")
        client.set_cookies({
            "auth_token": TWITTER_AUTH_TOKEN,
            "ct0": TWITTER_CT0,
        })
        return client

    # Method 2: Reuse saved cookies file
    if os.path.exists(COOKIES_FILE):
        log.info("Loading saved Twitter cookies…")
        client.load_cookies(COOKIES_FILE)
    else:
        log.info("Logging in to Twitter as @%s…", TWITTER_AUTH_USER)
        await client.login(
            auth_info_1=TWITTER_AUTH_EMAIL,
            auth_info_2=TWITTER_AUTH_USER,
            password=TWITTER_AUTH_PASS,
        )
        client.save_cookies(COOKIES_FILE)
        log.info("Login successful. Cookies saved.")

    return client


# ─────────────────────────────────────────────
# Text formatting helper
# ─────────────────────────────────────────────
def format_tweet_text(tweet) -> str:
    # Extract raw full text
    raw_text = getattr(tweet, 'full_text', None) or getattr(tweet, 'text', '')

    # Expand t.co URLs if tweet.urls is available
    urls_map = {}
    if hasattr(tweet, 'urls') and tweet.urls:
        for u in tweet.urls:
            if isinstance(u, dict):
                short = u.get('url')
                expanded = u.get('expanded_url') or u.get('display_url')
                if short and expanded:
                    urls_map[short] = expanded

    for short_url, expanded_url in urls_map.items():
        raw_text = raw_text.replace(short_url, expanded_url)

    # Remove remaining t.co links (media links / quote links)
    raw_text = re.sub(r'https://t\.co/\w+', '', raw_text)

    # Escape HTML special chars safely
    text = escape(raw_text.strip())

    # Convert @Username to clickable HTML link: <a href="https://x.com/Username">@Username</a>
    text = re.sub(r'@([A-Za-z0-9_]+)', r'<a href="https://x.com/\1">@\1</a>', text)

    return text.strip()


# ─────────────────────────────────────────────
# Main polling loop
# ─────────────────────────────────────────────
async def main():
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TWITTER_USERNAME]):
        log.error("Missing basic environment variables. Check TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TWITTER_USERNAME.")
        return

    if not (TWITTER_AUTH_TOKEN and TWITTER_CT0) and not all([TWITTER_AUTH_EMAIL, TWITTER_AUTH_USER, TWITTER_AUTH_PASS]):
        log.error("Missing Twitter auth details. Provide either TWITTER_AUTH_TOKEN & TWITTER_CT0 OR username, email & password.")
        return

    telegram = Bot(token=TELEGRAM_BOT_TOKEN)
    twitter  = await get_twitter_client()

    # Resolve the target user
    log.info("Looking up @%s…", TWITTER_USERNAME)
    target_user = await twitter.get_user_by_screen_name(TWITTER_USERNAME)
    log.info("Found user: %s (ID: %s)", target_user.name, target_user.id)

    seen_ids: set[str] = set()
    initialized = False

    log.info("🚀 Bot running — polling @%s every %ds", TWITTER_USERNAME, POLL_INTERVAL)

    while True:
        try:
            tweets = await target_user.get_tweets("Tweets", count=20)

            if not initialized:
                for t in tweets:
                    seen_ids.add(t.id)
                log.info("✅ Initialized with %d existing tweets. Watching for new ones…", len(seen_ids))
                initialized = True
            else:
                # Collect new tweets and post oldest first
                new_tweets = [t for t in tweets if t.id not in seen_ids]
                new_tweets.reverse()

                for tweet in new_tweets:
                    text = format_tweet_text(tweet)

                    # Extract media photo URLs
                    photos = []
                    if hasattr(tweet, 'media') and tweet.media:
                        for m in tweet.media:
                            m_type = getattr(m, 'type', None)
                            m_url = getattr(m, 'media_url', None) or getattr(m, 'source_url', None)
                            if m_type == 'photo' and m_url:
                                photos.append(m_url)

                    try:
                        if len(photos) == 1:
                            # Single native photo post with caption
                            await telegram.send_photo(
                                chat_id=TELEGRAM_CHANNEL_ID,
                                photo=photos[0],
                                caption=text,
                                parse_mode="HTML"
                            )
                        elif len(photos) > 1:
                            # Multiple native photos media group
                            media_group = [
                                InputMediaPhoto(media=url, caption=text if i == 0 else "", parse_mode="HTML")
                                for i, url in enumerate(photos[:10])
                            ]
                            await telegram.send_media_group(chat_id=TELEGRAM_CHANNEL_ID, media=media_group)
                        else:
                            # Text-only post
                            await telegram.send_message(
                                chat_id=TELEGRAM_CHANNEL_ID,
                                text=text,
                                parse_mode="HTML"
                            )

                        seen_ids.add(tweet.id)
                        log.info("📨 Posted tweet %s to Telegram", tweet.id)
                    except TelegramError as e:
                        log.error("Telegram error: %s", e)

        except TooManyRequests as e:
            # X rate-limited us — back off and retry
            wait = max(e.rate_limit_reset - asyncio.get_event_loop().time(), 60)
            log.warning("Rate limited by Twitter. Waiting %ds…", int(wait))
            await asyncio.sleep(wait)
            continue

        except Exception as e:
            log.exception("Unexpected error: %s", e)
            # If cookie session expired, delete and re-login next iteration
            if "Could not authenticate" in str(e) or "32" in str(e):
                log.warning("Session expired — deleting cookies, will re-login.")
                if os.path.exists(COOKIES_FILE):
                    os.remove(COOKIES_FILE)
                twitter = await get_twitter_client()

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
