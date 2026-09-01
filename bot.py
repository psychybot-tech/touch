import os
import asyncio
import logging
from dotenv import load_dotenv
from twikit import Client, TooManyRequests
from telegram import Bot
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
                    tweet_url = f"https://twitter.com/{TWITTER_USERNAME}/status/{tweet.id}"
                    message = f"🐦 @{TWITTER_USERNAME}\n\n{tweet.text}\n\n{tweet_url}"
                    try:
                        await telegram.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message)
                        seen_ids.add(tweet.id)
                        log.info("📨 Posted tweet %s", tweet.id)
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
