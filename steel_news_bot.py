from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import feedparser
import requests

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Tehran")
except Exception:
    # Fallback if the runner's tzdata is ever missing — Iran has used a
    # fixed UTC+03:30 offset (no DST) since 1401 (2022).
    TZ = timezone(timedelta(hours=3, minutes=30))
UTC = timezone.utc
BASE = Path(__file__).parent
STATE_FILE = BASE / "sent_links.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip() or CHAT_ID
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash-lite"

MAX_ITEMS_PER_RUN = 8
HTTP_TIMEOUT = 12
FEED_FETCH_WORKERS = 8
TELEGRAM_DELAY = 1.2

# gemini-2.5-flash-lite's free tier is 1,000 requests/day (Google's published
# limit as of writing). This cron runs every 5 minutes (~288 runs/day) and
# makes at most 1 Gemini call per run, so normal usage is ~288/day — well
# under the cap. This budget is a hard safety net for abnormal days (e.g.
# repeated fallback-model attempts): once hit, the bot stops calling Gemini
# for the rest of the Iran calendar day instead of burning through 429s.
DAILY_GEMINI_CALL_BUDGET = 900

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
    q = f"({query}) ({' OR '.join('site:' + d for d in domains)})" if domains else f"({query})"
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + (
        "&hl=fa&gl=IR&ceid=IR:fa" if fa else "&hl=en-US&gl=US&ceid=US:en"
    )


# Broad queries: no site: restriction — chaining ~17 domains onto every
# keyword query with OR produced compound queries Google News' search
# backend was silently returning near-empty results for. hl/gl already
# scope these to the right language/region; per-source coverage comes
# from the simple single-domain feeds appended below instead.
FEEDS = [rss(q, [], True) for q in FA_QUERIES] + [rss(q, [], False) for q in EN_QUERIES]
for d in [
    "reuters.com", "worldsteel.org", "steelorbis.com", "fastmarkets.com", "argusmedia.com",
    "spglobal.com", "steelradar.com", "mining.com", "irna.ir", "isna.ir", "ilna.ir",
    "donya-e-eqtesad.com", "ecoiran.com", "tejaratnews.com", "ime.co.ir", "imidro.gov.ir"
]:
    FEEDS.append(rss("steel OR فولاد OR iron ore OR آهن", [d], d in IRAN))


def state_default() -> dict[str, Any]:
    return {
        "version": 4,
        "sent_links": [], "sent_ids": [], "rejected_ids": [],
        "last_run_at": None,
        "gemini_calls": {"date": None, "count": 0},
    }


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


LOOKBACK_HOURS = 30  # generous rolling window — still "only recent news",
                      # but not brittle to an exact-calendar-date match


def is_recent(entry: Any) -> bool:
    d = pub_dt(entry)
    if d is None:
        return False
    age = datetime.now(TZ) - d
    return -timedelta(minutes=10) <= age <= timedelta(hours=LOOKBACK_HOURS)


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


def _fetch_feed(session: requests.Session, url: str,
                 seen_links: set, seen_ids: set, seen_rejected: set) -> tuple[dict[str, dict[str, Any]], int, int]:
    local: dict[str, dict[str, Any]] = {}
    raw = date_pass = 0
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT); r.raise_for_status()
        feed = feedparser.parse(r.content)
        for e in feed.entries:
            raw += 1
            x = item(e)
            if not x["title"] or not x["link"]: continue
            if not is_recent(e): continue
            date_pass += 1
            if not relevant_candidate(x["title"] + " " + x["summary"]): continue
            if x["link"] in seen_links or x["uid"] in seen_ids or x["uid"] in seen_rejected: continue
            local[x["link"]] = x
    except Exception as e:
        print(f"[warn] feed error ({url[:70]}...): {e}", file=sys.stderr)
    return local, raw, date_pass


def collect(s: dict[str, Any]) -> list[dict[str, Any]]:
    seen_links = set(s.get("sent_links", [])) | set(s.get("rejected_ids", []))
    seen_ids = set(s.get("sent_ids", []))
    seen_rejected = set(s.get("rejected_ids", []))
    out: dict[str, dict[str, Any]] = {}
    total_raw = total_date_pass = 0
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    with ThreadPoolExecutor(max_workers=FEED_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_feed, session, url, seen_links, seen_ids, seen_rejected) for url in FEEDS]
        for fut in as_completed(futures):
            local, raw, date_pass = fut.result()
            out.update(local); total_raw += raw; total_date_pass += date_pass
    print(f"[info] feed funnel: {total_raw} raw entries -> {total_date_pass} within last {LOOKBACK_HOURS}h -> {len(out)} matched keywords & new")
    return sorted(out.values(), key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=TZ))[:MAX_ITEMS_PER_RUN]


def gemini_prompt_batch(items: list[dict[str, Any]]) -> str:
    numbered = "\n\n".join(
        f'{i}. عنوان: {x["title"]}\nمتن RSS: {x["summary"]}\nمنبع: {x["source"]}'
        for i, x in enumerate(items, start=1)
    )
    return f'''تو ویراستار ارشد اخبار آهن و فولاد هستی. برای هر یک از خبرهای زیر یک آبجکت JSON بساز.

اگر خبر واقعاً درباره صنعت آهن و فولاد، سنگ‌آهن، مواد اولیه، تولید، قیمت، بازار، تجارت، صادرات/واردات، شرکت‌های فولادی یا سیاست مستقیم این صنعت نیست، relevant=false.
محتوای تبلیغاتی/آموزشی/غیرخبری هم relevant=false.
هیچ واقعیتی خارج از متن اختراع نکن. فارسی بنویس.

فقط یک آرایه JSON برگردان (بدون Markdown)، یک آبجکت برای هر خبر، به همین ترتیب شماره‌گذاری:
{{"index":1,"relevant":true,"news_type":"خبر","category":"بازار و قیمت","importance":"مهم","title":"...","summary":"...","analysis":"...","market_impact":"نامشخص"}}
category یکی از: بازار و قیمت، تولید، صادرات و واردات، بورس کالا، قوانین و سیاست‌گذاری، شرکت‌ها و کارخانه‌ها، مواد اولیه، بازار جهانی، سایر
market_impact یکی از: افزایشی، کاهشی، نامشخص

خبرها:
{numbered}'''


def _extract_json_array(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array in response")
    return json.loads(text[start:end + 1])


def analyze_batch(items: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], int]:
    """One Gemini call for the whole run instead of one per item — cuts API
    usage ~N-fold and means a single slow/failing call can't eat the run's
    time budget item by item. Returns ({}, calls_made) on total failure so
    the caller can tell 'no candidates' apart from 'Gemini was unreachable',
    and can charge the daily call budget for every real HTTP attempt made
    (a 404 or 429 still counts against the day's request quota).

    Also tries a short list of known-good models if the configured one
    404s (model name doesn't exist for this API key/version) — this is
    exactly what the usage dashboard showed happening, so the bot no
    longer depends on a human fixing the GEMINI_MODEL secret by hand."""
    if not GEMINI_KEY: raise RuntimeError("GEMINI_API_KEY is missing")
    if not items: return {}, 0

    fallback_models = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.0-flash"]
    models_to_try = [GEMINI_MODEL] + [m for m in fallback_models if m != GEMINI_MODEL]

    payload = {"contents":[{"parts":[{"text":gemini_prompt_batch(items)}]}],"generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}}
    last_err: Optional[Exception] = None
    calls_made = 0

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        for attempt in range(1, 4):
            try:
                r = requests.post(url, json=payload, timeout=90)
                calls_made += 1
                if r.status_code == 404:
                    print(f"[warn] Gemini model '{model}' not found (404) — trying next fallback model", file=sys.stderr)
                    last_err = RuntimeError(f"model '{model}' not found")
                    break  # try the next model, no point retrying a model that doesn't exist
                if r.status_code == 429:
                    # Quota/rate-limit is per API key, not per model — retrying
                    # or switching models won't fix it, so fail fast.
                    print(f"[warn] Gemini quota/rate-limited (429) on '{model}': {r.text[:200]}", file=sys.stderr)
                    return {}, calls_made
                if r.status_code in (500,502,503,504): raise RuntimeError(f"retryable status {r.status_code}")
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                parsed = _extract_json_array(text)
                if model != GEMINI_MODEL:
                    print(f"[info] used fallback model '{model}' (configured '{GEMINI_MODEL}' unavailable)")
                return {int(o["index"]): o for o in parsed if "index" in o}, calls_made
            except Exception as e:
                last_err = e
                if attempt == 3:
                    print(f"[warn] Gemini call failed on '{model}': {e}", file=sys.stderr)
                    break
                time.sleep(5 * attempt)

    print(f"[warn] Gemini batch failed on every model tried: {last_err}", file=sys.stderr)
    return {}, calls_made


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
    if not candidates:
        s["last_run_at"] = datetime.now(TZ).isoformat(); save_state(s)
        print(f"[done] sent=0, rejected=0, no candidates, today={datetime.now(TZ).date()}")
        return 0

    today_str = datetime.now(TZ).date().isoformat()
    gc = s.setdefault("gemini_calls", {"date": today_str, "count": 0})
    if gc.get("date") != today_str:
        gc["date"] = today_str; gc["count"] = 0
    if gc["count"] >= DAILY_GEMINI_CALL_BUDGET:
        print(
            f"[warn] daily Gemini call budget ({DAILY_GEMINI_CALL_BUDGET}) reached "
            f"({gc['count']} used) — skipping classification this run, will resume "
            "automatically at the next Iran calendar day.", file=sys.stderr,
        )
        s["last_run_at"] = datetime.now(TZ).isoformat(); save_state(s)
        return 0

    results, calls_made = analyze_batch(candidates)
    gc["count"] += calls_made
    gemini_unreachable = not results  # {} means the whole Gemini call failed

    sent = rejected = failed = 0
    for i, x in enumerate(candidates, start=1):
        a = results.get(i)
        if not a:
            failed += 1; continue
        if not bool(a.get("relevant")) or str(a.get("news_type")) == "غیرخبری":
            s["rejected_ids"].append(x["uid"]); rejected += 1; save_state(s); continue
        if send(message(x,a)):
            s["sent_links"].append(x["link"]); s["sent_ids"].append(x["uid"]); sent += 1; save_state(s); time.sleep(TELEGRAM_DELAY)
    s["last_run_at"] = datetime.now(TZ).isoformat(); save_state(s)
    print(f"[done] sent={sent}, rejected={rejected}, failed={failed}, gemini_calls_today={gc['count']}/{DAILY_GEMINI_CALL_BUDGET}, today={datetime.now(TZ).date()}")

    if gemini_unreachable:
        # Don't report success to GitHub Actions when nothing could actually
        # be classified — this is what makes the "failure()" alert step fire
        # instead of the run silently doing nothing every 5 minutes.
        raise RuntimeError(
            f"Gemini was unreachable for all {len(candidates)} candidate(s) this run "
            "(likely quota/rate-limit — check usage at aistudio.google.com)."
        )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
