# کران فولاد ⚙️ — Steel News Bot

ربات اخبار آهن و فولاد برای Telegram با اجرای خودکار GitHub Actions.

## قانون قطعی تاریخ
ربات فقط خبری را بررسی/ارسال می‌کند که **تاریخ انتشار دقیق آن پس از تبدیل به `Asia/Tehran` برابر با تاریخ امروز ایران باشد**.

- خبر دیروز: رد
- خبر قدیمی که RSS دوباره نشان دهد: رد
- خبر با تاریخ نامشخص: رد
- «۳۰ ساعت گذشته» یا «۲۴ ساعت گذشته»: در کد وجود ندارد

## ساختار
```text
steel-news-bot/
├── steel_news_bot.py
├── requirements.txt
├── sent_links.json
├── README.md
├── .gitignore
└── .github/
    └── workflows/
        └── steel-news.yml
```

## Secrets
در `Settings → Secrets and variables → Actions` بسازید:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_ADMIN_CHAT_ID` (اختیاری)
- `GEMINI_API_KEY`
- `GEMINI_MODEL` (اختیاری؛ پیش‌فرض در کد تعیین شده است)

## Workflow
فقط همین فایل باید Workflow باشد:
`.github/workflows/steel-news.yml`

**فایل `steel-news.yml` در ریشه Repository نسازید.**

## منابع
کد از Google News RSS با فیلتر دامنه برای منابع معتبر ایرانی و جهانی استفاده می‌کند و لینک خبر به منبع اصلی هدایت می‌شود. منابع عمومی و تخصصی شامل خبرگزاری‌های ایرانی، Reuters، Bloomberg، Financial Times، WSJ، CNBC، AP، World Steel Association، SteelOrbis، Fastmarkets، Argus، S&P Global، MEPS، MetalMiner، Mining.com و منابع تخصصی مشابه هستند.

## تست
پس از Upload:
1. Actions را باز کنید.
2. `Steel News Bot` را انتخاب کنید.
3. `Run workflow` را بزنید.
4. لاگ را بررسی کنید.
5. اجرای خودکار هر ۵ دقیقه فعال خواهد بود.

GitHub ممکن است Scheduled Workflow را در زمان بار زیاد با تأخیر اجرا کند؛ این محدودیت سرویس GitHub است، نه کد.
