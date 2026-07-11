
import os
import json
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import feedparser
from bs4 import BeautifulSoup
from supabase import create_client
import google.generativeai as genai
from pydantic import BaseModel

# ─────────────────────────── ENV ────────────────────────────
SUPABASE_URL    = os.getenv("SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or not GEMINI_API_KEY:
    raise Exception("Missing environment variables")

# ─────────────────────────── CLIENTS ────────────────────────
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# ─────────────────────────── APP ────────────────────────────
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────── RSS FEEDS ──────────────────────
RSS_URLS =[
    # ── World / General ──
    "http://feeds.bbci.co.uk/news/rss.xml",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "http://feeds.bbci.co.uk/news/uk/rss.xml",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",       # TOI World
    "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",     # TOI India
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",

    # ── Politics ──
    "http://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",

    # ── Business & Finance ──
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",         # TOI Business

    # ── Technology & Startups ──
    "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",        # TOI Tech
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://medium.com/feed/tag/technology",
    "https://medium.com/feed/tag/artificial-intelligence",

    # ── Space & Science ──
    "https://www.space.com/feeds/all",
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
    "https://www.sciencedaily.com/rss/all.xml",

    # ── Health ──
    "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
    "https://www.medicalnewstoday.com/rss",
    "https://www.who.int/rss-feeds/news-english.xml",

    # ── Sports ──
    "http://feeds.bbci.co.uk/sport/rss.xml",
    "https://www.espncricinfo.com/rss/content/story/feeds/6.xml",
    "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms",         # TOI Sports

    # ── Entertainment ──
    "http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    "https://variety.com/feed/",
    "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",      # TOI Entertainment
]


# ─────────────────────────── PROMPT ─────────────────────────
PROMPT = """
You are Nova, an award-winning digital news editor for "Slide" — a fast, mobile-first
news app read by busy, curious people who want to feel instantly informed and hooked
from the very first line.

Rewrite the article below into scroll-stopping, addictive-but-accurate news copy.

VOICE & STYLE RULES:
- Open the "news" field with a hook — a surprising fact, a stake, a tension, a "why
  this matters now" — never a flat "X announced Y today."
- Write like a sharp human journalist, not a press release: active voice, punchy and
  varied sentence lengths, no filler adjectives, no throat-clearing.
- Be 100% factually faithful to the source article. Never invent details, numbers,
  quotes, or outcomes that aren't in the article.
- 2–3 short, tight paragraphs. Every sentence has to earn its place.
- "headline": clear AND compelling — the kind of line that makes someone tap while
  scrolling — but never clickbait, never misleading.
- "notification": a push-notification teaser, under 80 characters, punchy enough to
  make someone open the app right now.
- "categories": sub-category first (lowercase), then the main category, from the list
  below. You may use multiple categories if genuinely relevant.

Output ONLY valid JSON. No markdown, no code fences, no commentary before or after.

Main categories:
general / global, sub categories - Breaking News, National News, World News, Politics, Government Policy, Elections, International Relations, Crime Reports, Cyber Crime
business & finance, sub categories - Stock Market, Banking & Loans, Cryptocurrency, Economy & Inflation, Corporate News, Investments & Funding
science & technology, sub categories - Technology News, Artificial Intelligence, Machine Learning, Robotics, Cybersecurity, Space & Astronomy, Space Missions, ISRO / NASA News, Gadgets & Reviews, Startup News, Tech Startups, AI Startups, Innovation & Research
sports, sub categories - Cricket, Football, Match Results, Player News, Tournaments, Sports Events
trending, sub categories - Viral News, Social Media Trends, Memes & Challenges, Internet Sensations, Public Buzz
entertainment, sub categories - Movies, Music, Celebrity News, OTT / Streaming, TV Shows
lifestyle & society, sub categories - Health & Wellness, Mental Health, Food & Nutrition, Travel, Fashion, Fitness

JSON FORMAT:
{
  "headline": "",
  "news": "",
  "notification": "",
  "categories": ""
}

Title: {title}
Article: {article}
"""

# ─────────────────────────── HELPERS ────────────────────────
def article_text(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        art = soup.find("article")
        if art:
            return art.get_text(" ", strip=True)
    except Exception:
        pass
    return ""


def already_exists(link: str) -> bool:
    res = supabase.table("news").select("id").eq("link", link).execute()
    return bool(res.data)


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


# ─────────────────────────── BACKGROUND JOB ─────────────────
# This runs AFTER the response is already sent to the client.
# Render's 30-second timeout does NOT apply to BackgroundTasks.
def run_news_fetch():
    """Fetch RSS → scrape → Gemini → insert into Supabase. Runs in background."""
    inserted = []
    errors   = []

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:15]:
            try:
                if already_exists(entry.link):
                    continue

                if not hasattr(entry, "published_parsed"):
                    continue

                pub_date = datetime(*entry.published_parsed[:6])
                if (datetime.now() - pub_date).days > 3:
                    continue

                article = article_text(entry.link)
                if not article:
                    continue

                prompt   = PROMPT.format(title=entry.title, article=article)
                response = model.generate_content(prompt)
                ai_json  = clean_json(response.text)

                image = ""
                if hasattr(entry, "media_thumbnail"):
                    image = entry.media_thumbnail[0].get("url", "")

                data = {
                    "headline":       ai_json["headline"],
                    "news":           ai_json["news"],
                    "notification":   ai_json["notification"],
                    "categories":     ai_json["categories"],
                    "link":           entry.link,
                    "image":          image,
                    "original":       entry.title,
                    "published_date": pub_date.isoformat(),
                }

                supabase.table("news").insert(data).execute()
                inserted.append(ai_json["headline"])

            except Exception as e:
                errors.append(str(e))

    print(f"[BG] News fetch done — inserted: {len(inserted)}, errors: {len(errors)}")
    if errors:
        print(f"[BG] Errors: {errors[:5]}")  # log first 5 only


# ─────────────────────────── ROUTES ─────────────────────────

@app.get("/health")
def health():
    return {"status": "running"}


@app.get("/news")
def trigger_news_fetch(background_tasks: BackgroundTasks):
    """
    Immediately returns 202 Accepted.
    The actual RSS fetch + Gemini pipeline runs in the background
    so it NEVER times out on Render's 30-second limit.
    Safe to call from a cron job or the frontend.
    """
    background_tasks.add_task(run_news_fetch)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": "News fetch started in background"}
    )


class GetNews(BaseModel):
    email: str


@app.post("/get_news")
def get_news(user: GetNews):
    """
    Returns up to 15 personalised news items for the user instantly
    from Supabase — no AI, no scraping, no timeouts.
    Simultaneously tells the client it can trigger /news in the background.
    """
    email = user.email

    # 1. Get user suggestions
    user_res = supabase.table("users") \
        .select("suggestions") \
        .eq("email", email) \
        .execute()

    if not user_res.data:
        return {"news": [], "trigger_refresh": True}

    suggestions = [s.lower() for s in (user_res.data[0]["suggestions"] or [])]

    # 2. Fetch latest 100 news rows (enough to filter from)
    news_res = supabase.table("news") \
        .select("*") \
        .order("published_date", desc=True) \
        .limit(100) \
        .execute()

    # 3. Match suggestions → categories
    final_news = []
    seen_links = set()

    for row in news_res.data:
        if row.get("link") in seen_links:
            continue

        categories = (row.get("categories") or "").lower()
        matched    = any(sug in categories for sug in suggestions) if suggestions else True

        if matched:
            final_news.append({
                "headline":     row["headline"],
                "news":         row["news"],
                "notification": row["notification"],
                "categories":   row["categories"],
                "link":         row["link"],
                "image":        row["image"],
                "published_date": row["published_date"],
            })
            seen_links.add(row.get("link"))

        if len(final_news) >= 15:   # ✅ return 15 instead of 5
            break

    # trigger_refresh tells the frontend to silently call /news in background
    return {"news": final_news, "trigger_refresh": len(final_news) < 5}
