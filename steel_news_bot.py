from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, date, timezone
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

TZ = ZoneInfo("Asia/Tehran")
UTC = timezone.utc
BASE = Path(__file__).parent
STATE_FILE = BASE / "sent_links.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip() or CHAT_ID
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite"

MAX_ITEMS_PER_RUN = 5
HTTP_TIMEOUT = 20
TELEGRAM_DELAY = 1.2

IRAN = [
    "irna.ir", "isna.ir", "ilna.ir", "mehrnews.com", "tasnimnews.com",
    "farsnews.ir", "khabaronline.ir", "donya-e-eqtesad.com", "eghtesadonline.com",
    "tejaratnews.com", "ecoiran.com", "boursepress.ir", "boursenews.ir",
    "sena.ir", "ibena.ir", "ime.co.ir", "imidro.gov.ir", "ispa.ir"
]
GLOBAL = [
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com", "apnews.com",
    "worldsteel.org", "steelorbis.com", "steelradar.com", "fastmarkets.com",
    "argusmedia.com", "spglobal.com", "mepsinternational.com", "metalminer.com",
    "mining.com", "steelmint.com", "scrapmonster.com"
]
FA_QUERIES = [
    "فولاد OR آهن OR شمش OR میلگرد OR ورق فولادی OR آهن اسفنجی OR گندله OR کنسانتره",
    "قیمت فولاد OR قیمت آهن OR قیمت میلگرد OR قیمت شمش",
    "صادرات فولاد OR واردات فولاد OR بورس کالا فولاد",
    "تولید فولاد OR کارخانه فولاد OR شرکت فولادی",
    "سنگ آهن OR آهن اسفنجی OR گندله OR کنسانتره OR قراضه",
]
EN_QUERIES = [
    "steel OR steelmaking OR steel mill OR steelmaker",
    "steel prices OR rebar prices OR HRC prices OR billet prices",
    "iron ore OR DRI OR direct reduced iron OR pellets OR scrap",
    "steel exports OR steel imports OR steel trade",
    "steel production OR blast furnace OR electric arc furnace",
    "China steel OR India steel OR Europe steel OR Turkey steel",
]
STRONG = [
    "فولاد", "steelmaking", "steel mill", "steelmaker", "steel price", "steel prices",
    "iron ore", "آهن اسفنجی", "direct reduced iron", "dri", "سنگ آهن", "سنگ‌آهن",
    "شمش", "billet", "بیلت", "slab", "اسلب", "میلگرد", "rebar", "تیرآهن",
    "ورق فولادی", "ورق گرم", "hrc", "crc", "گندله", "pellet", "کنسانتره",
    "concentrate", "قراضه", "scrap", "کک", "coke", "blast furnace",
    "electric arc furnace", "بورس کالا", "صادرات فولاد", "واردات فولاد",
    "تولید فولاد", "بازار فولاد"
]


def rss(query: str, domains: list[str], fa: bool) -> str:
    q = f"({query}) ({' OR '.join('site:' + d for d in domains)})"
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + (
        "&hl=fa&gl=IR&ceid=IR:fa" if fa else "&hl=en-US&gl=US&ceid=US:en"
    )


FEEDS = [rss(q, IRAN, True) for q in FA_QUERIES] + [rss(q, GLOBAL, False) for q in EN_QUERIES]
for d in [
    "reuters.com", "worldsteel.org", "steelorbis.com", "fastmarkets.com", "argusmedia.com",
    "spglobal.com", "steelradar.com", "mining.com", "irna.ir", "isna.ir", "ilna.ir",
    "donya-e-eqtesad.com", "ecoiran.com", "tejaratnews.com", "ime.co.ir", "imidro.gov.ir"
]:
    FEEDS.append(rss("steel OR فولاد OR iron ore OR آهن", [d], d in IRAN))


def state_default() -> dict[str, Any]:
    return {"version": 3, "sent_links": [], "sent_ids": [], "rejected_ids": [], "last_run_at": None}


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists(): return state_default()
    try: data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] state read failed: {e}", file=sys.stderr); return state_default()
    if isinstance(data, list):
        return {**state_default(), "sent_links": data}
    if not isinstance(data, dict): return state_default()
    s = state_default(); s.update(data); return s


def save_state(s: dict[str, Any]) -> None:
    for k in ("sent_links", "sent_ids", "rejected_ids"):
        s[k] = list(dict.fromkeys(s.get(k, [])))
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def norm(x: str) -> str:
    x = (x or "").replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", x).strip().lower()


def pub_dt(entry: Any) -> Optional[datetime]:
    for f in ("published_parsed", "updated_parsed"):
        p = entry.get(f)
        if p:
            try: return datetime(*p[:6], tzinfo=UTC).astimezone(TZ)
            except Exception: pass
    for f in ("published", "updated", "pubDate"):
        v = entry.get(f)
        if v:
            try:
                p = feedparser._parse_date(v)  # type: ignore[attr-defined]
                if p: return datetime(*p[:6], tzinfo=UTC).astimezone(TZ)
            except Exception: pass
    return None


def is_today(entry: Any, today: date) -> bool:
    d = pub_dt(entry)
    return d is not None and d.date() == today


def relevant_candidate(text: str) -> bool:
    t = norm(text)
    return any(norm(x) in t for x in STRONG)


def item(entry: Any) -> dict[str, Any]:
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    dt = pub_dt(entry)
    src = entry.get("source")
    source = src.get("title", "") if isinstance(src, dict) else ""
    uid = hashlib.sha256(f"{link}|{norm(title)}|{dt.isoformat() if dt else ''}".encode()).hexdigest()
    return {"title": title, "link": link, "summary": summary, "published_at": dt, "source": source, "uid": uid}


def collect(s: dict[str, Any]) -> list[dict[str, Any]]:
    today = datetime.now(TZ).date()
    seen_links = set(s.get("sent_links", [])) | set(s.get("rejected_ids", []))
    seen_ids = set(s.get("sent_ids", []))
    out: dict[str, dict[str, Any]] = {}
    session = requests.Session()
    session.headers["User-Agent"] = "Kran-Foolad-News-Bot/5.0"
    for url in FEEDS:
        try:
            r = session.get(url, timeout=HTTP_TIMEOUT); r.raise_for_status()
            feed = feedparser.parse(r.content)
            for e in feed.entries:
                x = item(e)
                if not x["title"] or not x["link"]: continue
                if not is_today(e, today): continue
                if not relevant_candidate(x["title"] + " " + x["summary"]): continue
                if x["link"] in seen_links or x["uid"] in seen_ids or x["uid"] in set(s.get("rejected_ids", [])): continue
                out[x["link"]] = x
        except Exception as e:
            print(f"[warn] feed error: {e}", file=sys.stderr)
    return sorted(out.values(), key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=TZ))[:MAX_ITEMS_PER_RUN]


def gemini_prompt(x: dict[str, Any]) -> str:
    return f'''تو ویراستار ارشد اخبار آهن و فولاد هستی.
عنوان: {x["title"]}
متن RSS: {x["summary"]}
منبع: {x["source"]}

فقط JSON معتبر برگردان.
اگر خبر واقعاً درباره صنعت آهن و فولاد، سنگ‌آهن، مواد اولیه، تولید، قیمت، بازار، تجارت، صادرات/واردات، شرکت‌های فولادی یا سیاست مستقیم این صنعت نیست، relevant=false.
محتوای تبلیغاتی/آموزشی/غیرخبری هم relevant=false.
هیچ واقعیتی خارج از متن اختراع نکن. فارسی بنویس.
ساختار:
{{"relevant":true,"news_type":"خبر","category":"بازار و قیمت","importance":"مهم","title":"...","summary":"...","analysis":"...","market_impact":"نامشخص"}}
category یکی از: بازار و قیمت، تولید، صادرات و واردات، بورس کالا، قوانین و سیاست‌گذاری، شرکت‌ها و کارخانه‌ها، مواد اولیه، بازار جهانی، سایر
market_impact یکی از: افزایشی، کاهشی، نامشخص'''


def analyze(x: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not GEMINI_KEY: raise RuntimeError("GEMINI_API_KEY is missing")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    payload = {"contents":[{"parts":[{"text":gemini_prompt(x)}]}],"generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}}
    for attempt in range(1, 6):
        try:
            r = requests.post(url, json=payload, timeout=60)
            if r.status_code in (429,500,502,503,504): raise RuntimeError(f"retryable status {r.status_code}")
            r.raise_for_status(); text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
            try: return json.loads(text)
            except Exception:
                m = re.search(r"\{.*\}", text, re.S); return json.loads(m.group(0)) if m else None
        except Exception as e:
            if attempt == 5:
                print(f"[warn] Gemini failed: {e}", file=sys.stderr); return None
            time.sleep(min(45, 4 * (2 ** (attempt - 1)) + random.uniform(0, 2)))
    return None


def send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID: raise RuntimeError("Telegram secrets are missing")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    if len(text) > 4096: text = text[:4050] + "\n…"
    for attempt in range(1, 5):
        try:
            r = requests.post(url, json={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML"}, timeout=30)
            if r.status_code in (429,500,502,503,504): raise RuntimeError(f"retryable status {r.status_code}")
            r.raise_for_status(); return True
        except Exception as e:
            if attempt == 4:
                print(f"[error] Telegram failed: {e}", file=sys.stderr); return False
            time.sleep(min(20, 2 ** attempt))
    return False


def message(x: dict[str, Any], a: dict[str, Any]) -> str:
    esc = lambda v: escape(str(v or ""))
    return (f"⚙️ <b>کران فولاد</b>\n\n"
            f"📰 <b>{esc(a.get('title') or x['title'])}</b>\n\n"
            f"<b>دسته:</b> {esc(a.get('category') or 'سایر')}\n"
            f"<b>اهمیت:</b> {esc(a.get('importance') or 'عادی')}\n"
            f"<b>اثر احتمالی بازار:</b> {esc(a.get('market_impact') or 'نامشخص')}\n\n"
            f"<b>خلاصه:</b>\n{esc(a.get('summary'))}\n\n"
            f"<b>تحلیل:</b>\n{esc(a.get('analysis'))}\n\n"
            f"🔗 <a href=\"{escape(x['link'], quote=True)}\">مشاهده منبع: {esc(x['source'] or 'منبع اصلی')}</a>")


def main() -> int:
    print(f"[info] Iran date: {datetime.now(TZ).date()}")
    print(f"[info] feeds: {len(FEEDS)} | Gemini: {GEMINI_MODEL}")
    for name, val in (("TELEGRAM_BOT_TOKEN",BOT_TOKEN),("TELEGRAM_CHAT_ID",CHAT_ID),("GEMINI_API_KEY",GEMINI_KEY)):
        if not val: raise RuntimeError(f"Missing secret: {name}")
    s = load_state(); candidates = collect(s)
    print(f"[info] candidate(s) from today: {len(candidates)}")
    sent = rejected = 0
    for x in candidates:
        a = analyze(x)
        if not a: continue
        if not bool(a.get("relevant")) or str(a.get("news_type")) == "غیرخبری":
            s["rejected_ids"].append(x["uid"]); rejected += 1; save_state(s); continue
        if send(message(x,a)):
            s["sent_links"].append(x["link"]); s["sent_ids"].append(x["uid"]); sent += 1; save_state(s); time.sleep(TELEGRAM_DELAY)
    s["last_run_at"] = datetime.now(TZ).isoformat(); save_state(s)
    print(f"[done] sent={sent}, rejected={rejected}, today={datetime.now(TZ).date()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
