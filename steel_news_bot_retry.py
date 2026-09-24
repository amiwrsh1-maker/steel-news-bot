"""
کران فولاد — ربات اخبار بازار آهن و فولاد

نسخه پایدار:
- فقط اخبار «امروز به وقت ایران» را بررسی می‌کند.
- خبر قدیمی را حتی اگر RSS تازه آن را برگرداند، منتشر نمی‌کند.
- خبر ارسال‌شده را در state دائمی نگه می‌دارد تا دوباره ارسال نشود.
- اگر GitHub Actions از نو اجرا شود، از اول آرشیو را ارسال نمی‌کند.
- برای هر خبر فقط یک فراخوانی Gemini انجام می‌شود.
- خروجی هر خبر: تیتر، خلاصه، تحلیل کوتاه، اثر احتمالی بازار و لینک منبع.
- خطاها باعث ارسال خبر ناقص یا ثبت اشتباه به عنوان «ارسال‌شده» نمی‌شوند.
"""

import json
import os
import re
import sys
import time
import hashlib
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from html import escape

import feedparser
import requests


# ============================================================
# 1) تنظیمات
# ============================================================

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

# فیلتر اولیه سریع. وجود «آهن» به تنهایی کافی نیست؛
# بعد از این مرحله Gemini ارتباط واقعی خبر را بررسی می‌کند.
STEEL_TERMS = [
    "فولاد", "steel", "steelmaking", "steel mill", "steelmaker",
    "آهن اسفنجی", "آهن‌اسفنجی", "direct reduced iron", "dri",
    "سنگ آهن", "سنگ‌آهن", "iron ore",
    "شمش", "billet", "بیلت", "slab", "اسلب",
    "میلگرد", "rebar", "تیرآهن", "ورق فولادی", "ورق سیاه",
    "ورق گرم", "ورق سرد", "hrc", "crc",
    "گندله", "pellet", "کنسانتره", "concentrate",
    "قراضه", "scrap", "کک", "coke",
    "ذوب", "کوره", "فولاد مبارکه", "ذوب آهن",
    "بورس کالا", "صادرات فولاد", "واردات فولاد",
    "تولید فولاد", "قیمت فولاد", "بازار فولاد",
]

# کلمات عمومی که به تنهایی نباید خبر را فولادی کنند.
WEAK_TERMS = {"آهن", "steel", "iron"}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or TELEGRAM_CHAT_ID

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
if not GEMINI_MODEL or GEMINI_MODEL.startswith("gemini-2.5-"):
    GEMINI_MODEL = "gemini-3.5-flash-lite"

TIMEZONE = ZoneInfo("Asia/Tehran")
STATE_FILE = Path(__file__).parent / "sent_links.json"

# حداکثر تعداد خبرهایی که یک اجرای ربات پردازش می‌کند.
# چون اجرا هر ۵ دقیقه است، معمولاً مقدار زیادی نخواهد بود.
MAX_ITEMS_PER_RUN = 5

# فاصله بین پیام‌های تلگرام
SEND_DELAY_SECONDS = 1.0

# Gemini sometimes returns temporary 503/timeouts. Retry only transient errors.
GEMINI_RETRIES = 2
GEMINI_RETRY_DELAYS = (4, 10)

# حداکثر طول پیام تلگرام
TELEGRAM_MAX_LEN = 4096

# برای جلوگیری از اینکه یک RSS خراب کل اجرا را متوقف کند
HTTP_TIMEOUT = 20


# ============================================================
# 2) State دائمی
# ============================================================

def empty_state() -> dict:
    return {
        "version": 2,
        "sent_links": [],
        "sent_ids": [],
        "rejected_ids": [],
        "last_run_at": None,
    }


def load_state() -> dict:
    """State جدید را می‌خواند و فرمت قدیمیِ list را هم پشتیبانی می‌کند."""
    if not STATE_FILE.exists():
        return empty_state()

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[warn] state file could not be read: {exc}", file=sys.stderr)
        return empty_state()

    # نسخه قبلی فایل فقط یک list از URLها بود.
    if isinstance(data, list):
        return {
            "version": 2,
            "sent_links": data,
            "sent_ids": [],
            "rejected_ids": [],
            "last_run_at": None,
        }

    if not isinstance(data, dict):
        return empty_state()

    data.setdefault("version", 2)
    data.setdefault("sent_links", [])
    data.setdefault("sent_ids", [])
    data.setdefault("rejected_ids", [])
    data.setdefault("last_run_at", None)
    return data


def save_state(state: dict) -> None:
    """State را اتمیک ذخیره می‌کند تا قطع ناگهانی فایل خراب نکند."""
    state["version"] = 2

    # تاریخچه عمداً truncate نمی‌شود؛ هدف این است که خبر قدیمی
    # هیچ‌وقت بعداً دوباره ارسال نشود.
    state["sent_links"] = list(dict.fromkeys(state.get("sent_links", [])))
    state["sent_ids"] = list(dict.fromkeys(state.get("sent_ids", [])))
    state["rejected_ids"] = list(dict.fromkeys(state.get("rejected_ids", [])))

    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_FILE)


def remember_sent(state: dict, entry: dict) -> None:
    link = entry.get("link", "")
    uid = entry.get("uid", "")
    if link:
        state["sent_links"].append(link)
    if uid:
        state["sent_ids"].append(uid)


def already_seen(state: dict, entry: dict) -> bool:
    uid = entry.get("uid", "")
    link = entry.get("link", "")
    return (
        link in set(state.get("sent_links", []))
        or uid in set(state.get("sent_ids", []))
        or uid in set(state.get("rejected_ids", []))
    )


def already_sent(state: dict, entry: dict) -> bool:
    return (
        entry.get("link", "") in set(state.get("sent_links", []))
        or entry.get("uid", "") in set(state.get("sent_ids", []))
    )


def remember_rejected(state: dict, entry: dict) -> None:
    uid = entry.get("uid", "")
    if uid:
        state["rejected_ids"].append(uid)


# ============================================================
# 3) تاریخ و زمان خبر
# ============================================================

def entry_datetime(entry) -> Optional[datetime]:
    """
    تاریخ واقعی انتشار/به‌روزرسانی RSS را پیدا می‌کند.
    ترتیب: published_parsed سپس updated_parsed.
    """
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                # parsed معمولاً time.struct_time به UTC است.
                dt = datetime(*parsed[:6], tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(TIMEZONE)
            except Exception:
                pass

    # بعضی RSSها تاریخ را به شکل رشته می‌دهند.
    for field in ("published", "updated"):
        value = entry.get(field)
        if not value:
            continue
        try:
            parsed = feedparser._parse_date(value)  # type: ignore[attr-defined]
            if parsed:
                dt = datetime(*parsed[:6], tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(TIMEZONE)
        except Exception:
            pass

    return None


def is_today(entry, today: date) -> bool:
    dt = entry_datetime(entry)
    return dt is not None and dt.date() == today


# ============================================================
# 4) نرمال‌سازی و فیلتر اولیه
# ============================================================

def normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\u200c", " ")
    text = text.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def has_strong_steel_term(text: str) -> bool:
    normalized = normalize_text(text)

    strong = 0
    for term in STEEL_TERMS:
        if normalize_text(term) in normalized:
            if normalize_text(term) not in {normalize_text(x) for x in WEAK_TERMS}:
                return True
            strong += 1

    # «آهن» یا «steel» به تنهایی کافی نیست.
    # فقط وقتی یکی از این واژه‌های ضعیف با زمینه‌ای دیگر همراه باشد
    # اجازه عبور به AI را می‌دهیم.
    context_terms = [
        "قیمت", "بازار", "تولید", "صادرات", "واردات", "کارخانه",
        "فروش", "خرید", "بورس", "شمش", "میلگرد", "ورق", "معدن",
        "سنگ", "گندله", "کنسانتره", "فولاد", "rebar", "billet",
        "slab", "hrc", "scrap", "ore", "mill",
    ]
    return strong > 0 and any(normalize_text(x) in normalized for x in context_terms)


# ============================================================
# 5) شناسه خبر و حذف تکراری‌های RSS
# ============================================================

def entry_uid(entry) -> str:
    link = (entry.get("link") or "").strip()
    title = normalize_text(entry.get("title", ""))
    dt = entry_datetime(entry)
    date_part = dt.isoformat() if dt else ""
    raw = f"{link}|{title}|{date_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def entry_to_dict(entry) -> dict:
    source = entry.get("source")
    source_title = ""
    if isinstance(source, dict):
        source_title = source.get("title", "") or ""

    return {
        "title": entry.get("title", "").strip(),
        "summary": entry.get("summary", "").strip(),
        "link": entry.get("link", "").strip(),
        "source": source_title,
        "published_at": entry_datetime(entry),
        "uid": entry_uid(entry),
    }


def collect_today_entries(state: dict) -> list[dict]:
    today = datetime.now(TIMEZONE).date()
    collected = {}

    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)

            if parsed.bozo and not parsed.entries:
                print(f"[warn] RSS failed: {feed_url}", file=sys.stderr)
                continue

            for raw_entry in parsed.entries:
                item = entry_to_dict(raw_entry)

                if not item["link"]:
                    continue

                # قانون اصلی: فقط تاریخ امروز ایران.
                if not is_today(raw_entry, today):
                    continue

                # فیلتر سریع.
                searchable = f'{item["title"]} {item["summary"]}'
                if not has_strong_steel_term(searchable):
                    continue

                if already_seen(state, item):
                    continue

                # یک خبر ممکن است از چند RSS وارد شود.
                # UID و URL هر دو برای dedupe استفاده می‌شوند.
                key = item["link"] or item["uid"]
                collected[key] = item

        except Exception as exc:
            print(f"[warn] feed error: {exc}", file=sys.stderr)

    entries = list(collected.values())

    # قدیمی‌ترها اول؛ این کار ترتیب زمانی کانال را مرتب‌تر می‌کند.
    entries.sort(key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=TIMEZONE))

    return entries[:MAX_ITEMS_PER_RUN]


# ============================================================
# 6) Gemini — فقط یک فراخوانی برای هر خبر
# ============================================================

def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def build_gemini_prompt(entry: dict) -> str:
    return f"""
تو ویراستار و تحلیلگر حرفه‌ای اخبار بازار آهن و فولاد ایران هستی.

خبر زیر را بررسی کن.

عنوان منبع:
{entry['title']}

متن/خلاصه منبع:
{entry['summary']}

منبع:
{entry['source']}

قوانین:
1) اول مشخص کن خبر واقعاً درباره صنعت آهن و فولاد، بازار فولاد، تولید،
   قیمت، صادرات، واردات، مواد اولیه، شرکت‌های فولادی یا سیاست‌های مستقیم
   مرتبط با این صنعت هست یا نه.
2) اگر صرفاً کلمه «آهن» یا «steel» در یک موضوع نامرتبط آمده، relevant=false.
3) اگر متن برای تحلیل اقتصادی کافی نیست، چیزی اختراع نکن.
4) خروجی کاملاً فارسی باشد، حتی category و سایر مقادیر متنی.
5) تیتر کوتاه، حرفه‌ای و خبری باشد و معنی خبر را تغییر ندهد.
6) خلاصه حداکثر 3 جمله کوتاه باشد.
7) تحلیل کوتاه حداکثر 2 جمله باشد و فقط بر اساس اطلاعات خبر و استنباط
   محتاطانه اقتصادی/بازاری نوشته شود.
8) اثر بازار فقط در صورت امکان استنباط معقول تعیین شود؛ در غیر این صورت
   «نامشخص» بنویس.
9) اگر متن تبلیغاتی، راهنمای خرید، مقاله عمومی یا محتوای غیرخبری است،
   news_type را «غیرخبری» قرار بده و relevant=false.
10) از ادعاهای خارج از متن خبر استفاده نکن.

فقط JSON معتبر و بدون Markdown برگردان، دقیقاً با این ساختار:
{{
  "relevant": true,
  "news_type": "خبر",
  "category": "بازار و قیمت",
  "importance": "مهم",
  "title": "تیتر فارسی کوتاه",
  "summary": "خلاصه کوتاه فارسی",
  "analysis": "تحلیل کوتاه فارسی",
  "market_impact": "افزایشی"
}}

مقادیر مجاز:
news_type: «خبر» یا «غیرخبری»
category: «بازار و قیمت»، «تولید»، «صادرات و واردات»، «بورس کالا»،
«قوانین و سیاست‌گذاری»، «شرکت‌ها و کارخانه‌ها»، «مواد اولیه»، «بازار جهانی»، «سایر»
importance: «مهم» یا «عادی»
market_impact: «افزایشی»، «کاهشی»، «نامشخص»
""".strip()


def analyze_with_gemini(entry: dict) -> Optional[dict]:
    if not GEMINI_API_KEY:
        print("[warn] GEMINI_API_KEY is not configured.", file=sys.stderr)
        return None

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [
            {"parts": [{"text": build_gemini_prompt(entry)}]}
        ],
        "generationConfig": {
            "temperature": 0.15,
            "responseMimeType": "application/json",
        },
    }

    last_exc = None

    for attempt in range(GEMINI_RETRIES + 1):
        try:
            response = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": GEMINI_API_KEY,
                },
                json=payload,
                timeout=25,
            )

            # 503/429 and network timeouts are transient; retry them.
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()

            response.raise_for_status()
            data = response.json()

            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(strip_code_fences(text))

            required = [
                "relevant", "news_type", "category", "importance",
                "title", "summary", "analysis", "market_impact",
            ]
            if not all(key in result for key in required):
                raise ValueError("Gemini JSON is missing required fields.")

            return result

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status in (429, 500, 502, 503, 504) or status is None
            if transient and attempt < GEMINI_RETRIES:
                delay = GEMINI_RETRY_DELAYS[attempt]
                print(
                    f"[warn] Gemini temporary failure for '{entry['title']}' "
                    f"(attempt {attempt + 1}/{GEMINI_RETRIES + 1}); retrying in {delay}s: {exc}",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            break
        except Exception as exc:
            last_exc = exc
            break

    print(f"[warn] Gemini failed for '{entry['title']}': {last_exc}", file=sys.stderr)
    return None


# ============================================================
# 7) Telegram
# ============================================================

def telegram_request(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Telegram credentials are missing.")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    response = requests.post(url, data=payload, timeout=20)
    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))

    return data


def build_telegram_message(entry: dict, ai: dict) -> str:
    title = escape(str(ai.get("title") or entry["title"]).strip())
    summary = escape(str(ai.get("summary") or "").strip())
    analysis = escape(str(ai.get("analysis") or "").strip())
    category = escape(str(ai.get("category") or "سایر").strip())
    impact = str(ai.get("market_impact") or "نامشخص").strip()
    importance = str(ai.get("importance") or "عادی").strip()
    source = escape(str(entry.get("source") or "منبع خبر").strip())
    link = entry.get("link", "")

    impact_icon = {
        "افزایشی": "📈",
        "کاهشی": "📉",
        "نامشخص": "⚪",
    }.get(impact, "⚪")

    category_icon = {
        "بازار و قیمت": "💰",
        "تولید": "🏭",
        "صادرات و واردات": "🚢",
        "بورس کالا": "📊",
        "قوانین و سیاست‌گذاری": "⚖️",
        "شرکت‌ها و کارخانه‌ها": "🏢",
        "مواد اولیه": "⛏️",
        "بازار جهانی": "🌍",
    }.get(category, "📰")

    important_line = "🚨 <b>خبر مهم</b>\n" if importance == "مهم" else ""

    return (
        f"{important_line}"
        f"{category_icon} <b>{title}</b>\n\n"
        f"📌 <b>خلاصه خبر:</b>\n{summary}\n\n"
        f"📊 <b>تحلیل کوتاه:</b>\n{analysis}\n\n"
        f"🎯 <b>اثر احتمالی بر بازار:</b>\n"
        f"{impact_icon} {escape(impact)}\n\n"
        f'📰 <b>منبع:</b> <a href="{escape(link, quote=True)}">مشاهده خبر</a>\n'
        f"({source})\n\n"
        f"⚙️ <b>کران فولاد</b>"
    )


def send_telegram_message(text: str) -> None:
    # پیام‌های ما عمداً کوتاه‌اند، اما این محافظ برای Telegram 4096 است.
    if len(text) > TELEGRAM_MAX_LEN:
        text = text[: TELEGRAM_MAX_LEN - 20] + "\n…"

    telegram_request(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
    )


def send_admin_alert(message: str) -> None:
    if not TELEGRAM_ADMIN_CHAT_ID or not TELEGRAM_BOT_TOKEN:
        return

    try:
        telegram_request(
            "sendMessage",
            {
                "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                "text": f"⚠️ <b>خطای ربات کران فولاد</b>\n\n{escape(message)}",
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
    except Exception as exc:
        print(f"[warn] could not send admin alert: {exc}", file=sys.stderr)


# ============================================================
# 8) اجرای اصلی
# ============================================================

def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[error] TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not RSS_FEEDS:
        print("[error] RSS_FEEDS is empty.", file=sys.stderr)
        sys.exit(1)

    if not GEMINI_API_KEY:
        msg = "GEMINI_API_KEY تنظیم نشده است؛ خبرها عمداً بدون تأیید AI منتشر نمی‌شوند."
        print(f"[error] {msg}", file=sys.stderr)
        send_admin_alert(msg)
        sys.exit(1)

    state = load_state()
    today = datetime.now(TIMEZONE).date()

    print(f"[info] Iran date: {today.isoformat()}")
    print(f"[info] last run: {state.get('last_run_at')}")

    entries = collect_today_entries(state)
    print(f"[info] candidate(s) from today: {len(entries)}")

    sent_count = 0
    skipped_count = 0

    for entry in entries:
        # دوباره قبل از AI بررسی می‌کنیم؛ برای اجرای طولانی/تکراری.
        if already_sent(state, entry):
            continue

        ai = analyze_with_gemini(entry)

        # اگر Gemini خطا داد، خبر را sent ثبت نمی‌کنیم.
        # اجرای بعدی دوباره آن را امتحان خواهد کرد.
        if ai is None:
            skipped_count += 1
            continue

        # خبر نامرتبط/غیرخبری هرگز منتشر نمی‌شود.
        if not ai.get("relevant") or ai.get("news_type") != "خبر":
            print(f"[info] rejected by AI: {entry['title']}")
            # خبر نامرتبط/غیرخبری را به‌عنوان rejected ذخیره می‌کنیم
            # تا در اجرای بعدی دوباره سهمیه Gemini را مصرف نکند.
            remember_rejected(state, entry)
            save_state(state)
            continue

        message = build_telegram_message(entry, ai)

        try:
            send_telegram_message(message)

            # فقط بعد از موفقیت واقعی Telegram، خبر را sent می‌کنیم.
            remember_sent(state, entry)
            state["last_run_at"] = datetime.now(TIMEZONE).isoformat()
            save_state(state)

            sent_count += 1
            print(f"[ok] sent: {entry['title']}")
            time.sleep(SEND_DELAY_SECONDS)

        except Exception as exc:
            error = f"ارسال خبر شکست خورد: {entry['title']}\n{exc}"
            print(f"[error] {error}", file=sys.stderr)
            send_admin_alert(error)
            # اجرای بعدی دوباره تلاش می‌کند.
            continue

    # در صورت نبود خبر جدید، state بدون تغییر می‌ماند تا هر اجرای ۵ دقیقه‌ای
    # باعث commit جدید در GitHub نشود.
    print(
        f"[done] sent={sent_count}, skipped={skipped_count}, "
        f"today={today.isoformat()}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        send_admin_alert(f"خطای جدی در اجرای ربات:\n{exc}")
        sys.exit(1)
