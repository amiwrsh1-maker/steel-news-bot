"""
Steel News Telegram Bot
------------------------
Reads a list of RSS feeds, finds entries published since the last run,
sends the batch to Gemini for a short per-item summary + an overall
conclusion, and posts the result to Telegram.

Designed to be run on a schedule (e.g. via GitHub Actions, cron, etc.).
State (which links were already sent) is kept in a small JSON file so the
same news item is never sent twice.
"""

import json
import os
import sys
import time
from pathlib import Path

import feedparser
import requests

# ---------------------------------------------------------------------------
# 1. CONFIGURATION — edit this part
# ---------------------------------------------------------------------------

# RSS feed URLs to monitor. Add / remove as many as you like.
# These use Google News' public RSS search — no need to hunt for each site's
# own (often unstable) feed URL. The `site:` operator restricts a query to
# one domain; combine keywords with OR inside parentheses.
RSS_FEEDS = [
    # General Persian search — any Iranian outlet
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%29&hl=fa&gl=IR&ceid=IR:fa",

    # General world steel-price search — English
    "https://news.google.com/rss/search?q=steel%20price&hl=en-US&gl=US&ceid=US:en",

    # دنیای اقتصاد — فولاد/آهن/شمش/میلگرد
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Adonya-e-eqtesad.com&hl=fa&gl=IR&ceid=IR:fa",

    # اقتصاد آنلاین
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aeghtesadonline.com&hl=fa&gl=IR&ceid=IR:fa",

    # تجارت‌نیوز
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Atejaratnews.com&hl=fa&gl=IR&ceid=IR:fa",

    # ایلنا
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Ailna.ir&hl=fa&gl=IR&ceid=IR:fa",

    # ایسنا
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aisna.ir&hl=fa&gl=IR&ceid=IR:fa",

    # بورس نیوز
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aboursenews.ir&hl=fa&gl=IR&ceid=IR:fa",

    # بورس ۲۴
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Abourse24.ir&hl=fa&gl=IR&ceid=IR:fa",

    # سنا — پایگاه خبری بازار سرمایه
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Asena.ir&hl=fa&gl=IR&ceid=IR:fa",

    # اکوایران
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aecoiran.com&hl=fa&gl=IR&ceid=IR:fa",

    # ایبنا — خبرگزاری بانک، بیمه، بورس
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aibena.ir&hl=fa&gl=IR&ceid=IR:fa",

    # اقتصادنیوز
    "https://news.google.com/rss/search?q=%28%D9%81%D9%88%D9%84%D8%A7%D8%AF%20OR%20%D8%A2%D9%87%D9%86%20OR%20%D8%B4%D9%85%D8%B4%20OR%20%D9%85%DB%8C%D9%84%DA%AF%D8%B1%D8%AF%29%20site%3Aeghtesadnews.com&hl=fa&gl=IR&ceid=IR:fa",
]

# Any other Iranian site you want included: copy one of the lines above and
# just change the domain after `site:` (keep everything else the same).

# Only send items whose title or summary contains at least one of these
# keywords (case-insensitive). Leave the list empty to send everything.
KEYWORDS = [
    "فولاد",
    "آهن",
    "شمش",
    "میلگرد",
    "steel",
    "iron ore",
    "rebar",
    "billet",
]

# Telegram credentials come from environment variables (never hard-code them).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Gemini API key (free tier at aistudio.google.com) — used to summarize the
# batch of news and write a short conclusion. Optional: if not set, the bot
# falls back to sending plain title+link messages with no summary.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash-lite"  # free-tier model as of writing

# File used to remember which links were already sent, so we don't repeat them.
STATE_FILE = Path(__file__).parent / "sent_links.json"

# Safety cap: don't spam the chat if a feed suddenly has 200 "new" items
# (e.g. the very first run). Increase if you want more history the first time.
MAX_ITEMS_PER_RUN = 40

# Telegram hard limit on a single message's length.
TELEGRAM_MAX_LEN = 4096


# ---------------------------------------------------------------------------
# 2. STATE HANDLING
# ---------------------------------------------------------------------------

def load_sent_links() -> set:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_links(links: set) -> None:
    # Keep the file from growing forever — cap at the most recent 2000 links.
    trimmed = list(links)[-2000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 3. FETCHING & FILTERING
# ---------------------------------------------------------------------------

def matches_keywords(entry) -> bool:
    if not KEYWORDS:
        return True
    text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
    return any(kw.lower() in text for kw in KEYWORDS)


def get_new_entries(sent_links: set) -> list:
    new_entries = []
    for feed_url in RSS_FEEDS:
        parsed = feedparser.parse(feed_url)
        if parsed.bozo and not parsed.entries:
            print(f"[warn] could not read feed: {feed_url}", file=sys.stderr)
            continue

        for entry in parsed.entries:
            link = entry.get("link")
            if not link or link in sent_links:
                continue
            if not matches_keywords(entry):
                continue
            new_entries.append(entry)

    return new_entries[:MAX_ITEMS_PER_RUN]


# ---------------------------------------------------------------------------
# 4. SUMMARIZATION (Gemini)
# ---------------------------------------------------------------------------

def build_summary_prompt(entries: list) -> str:
    items_text = ""
    for i, entry in enumerate(entries, start=1):
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        source = entry.get("source", {}).get("title") if entry.get("source") else ""
        items_text += f"\n{i}. عنوان: {title}\n   منبع: {source}\n   متن: {summary}\n"

    return (
        "تو یک تحلیلگر بازار فولاد و آهن هستی. خبرهای زیر را بررسی کن.\n"
        "برای هر خبر، یک خلاصه‌ی حداکثر ۳ خطی به فارسی بنویس (روان و خبری، "
        "بدون اضافه‌گویی). سپس در انتها یک بخش با عنوان «جمع‌بندی» بیاور "
        "و در ۴ تا ۶ خط، مهم‌ترین روند یا نکته‌ی مشترک بین این اخبار "
        "(مثلاً جهت قیمت، عرضه/تقاضا، تصمیمات دولتی و غیره) را تحلیل و "
        "نتیجه‌گیری کن. خروجی را با فرمت زیر و بدون Markdown اضافه بده:\n\n"
        "۱. [عنوان کوتاه]\n[خلاصه ۳ خطی]\n\n۲. ...\n\nجمع‌بندی:\n[تحلیل نهایی]\n\n"
        f"خبرها:\n{items_text}"
    )


def summarize_with_gemini(entries: list):
    if not GEMINI_API_KEY:
        return None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    payload = {
        "contents": [
            {"parts": [{"text": build_summary_prompt(entries)}]}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:  # noqa: BLE001 — log and fall back gracefully
        print(f"[warn] Gemini summarization failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 5. SENDING TO TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    response = requests.post(url, data=payload, timeout=15)
    if response.status_code != 200:
        print(f"[error] Telegram API error: {response.text}", file=sys.stderr)


def chunk_text(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list:
    """Split long text into Telegram-sized chunks, breaking on line breaks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def format_plain_message(entry) -> str:
    """Fallback format used when no Gemini summary is available."""
    title = entry.get("title", "بدون عنوان")
    link = entry.get("link", "")
    source = entry.get("source", {}).get("title") if entry.get("source") else None
    header = f"📰 {title}"
    if source:
        header += f"\n🔗 {source}"
    return f"{header}\n{link}"


def build_links_footer(entries: list) -> str:
    lines = ["🔗 لینک منابع:"]
    for i, entry in enumerate(entries, start=1):
        lines.append(f"{i}. {entry.get('link', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. MAIN
# ---------------------------------------------------------------------------

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[error] TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set "
            "as environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not RSS_FEEDS:
        print(
            "[error] RSS_FEEDS is empty — add at least one feed URL in the "
            "CONFIGURATION section.",
            file=sys.stderr,
        )
        sys.exit(1)

    sent_links = load_sent_links()
    new_entries = get_new_entries(sent_links)

    if not new_entries:
        print("No new steel news found this run.")
        return

    summary = summarize_with_gemini(new_entries)

    if summary:
        # Send the AI summary + conclusion, then a compact list of source links.
        for chunk in chunk_text(f"📊 اخبار فولاد و آهن\n\n{summary}"):
            send_telegram_message(chunk)
            time.sleep(1)
        for chunk in chunk_text(build_links_footer(new_entries)):
            send_telegram_message(chunk)
    else:
        # Fallback: no Gemini key configured, or the call failed — send
        # each item as a plain message like before.
        for entry in new_entries:
            send_telegram_message(format_plain_message(entry))
            time.sleep(1)

    for entry in new_entries:
        sent_links.add(entry.get("link"))

    save_sent_links(sent_links)
    print(f"Processed {len(new_entries)} new item(s).")


if __name__ == "__main__":
    main()
