# main.py
import os
import json
from datetime import datetime
from fastapi import FastAPI
import requests
import feedparser
from bs4 import BeautifulSoup
from supabase import create_client
import google.generativeai as genai
from pydantic import BaseModel

# ---------------- ENV ----------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI()

if not SUPABASE_URL or not SUPABASE_KEY or not GEMINI_API_KEY:
    raise Exception("Missing environment variables")

# ---------------- CLIENTS ----------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")  # ✅ FIXED

@app.get("/health")
def health():
    return{"status":"running"}

# ---------------- RSS ----------------
RSS_URLS = [
  "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/front_page/rss.xml",
  "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/world/rss.xml",
  "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/business/rss.xml",
  "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/technology/rss.xml",
  "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/uk_politics/rss.xml",
]

# ---------------- PROMPT ----------------
PROMPT = """
You are a professional news writer.

Rewrite the news clearly and attractively.

Rules:
- Output ONLY valid JSON
- 2–3 short paragraphs
- Categories = sub-category first below , in small letters ,then main category,you can use multiple categories 

Main categories:
general / global, sub categories - Breaking News,
National News,
World News,
Politics,
Government Policy,
Elections,
International Relations,
Crime Reports,
Cyber Crime
business & finance,
sub categories - Stock Market,
Banking & Loans,
Cryptocurrency,
Economy & Inflation,
Corporate News,
Investments & Funding,
science & technology,
sub categories - Technology News,
Artificial Intelligence,
Machine Learning,
Robotics,
Cybersecurity,
Space & Astronomy,
Space Missions,
ISRO / NASA News,
Gadgets & Reviews,
Startup News,
Tech Startups,
AI Startups,
Innovation & Research
sports,
sub categories - Cricket,
Football,
Match Results,
Player News,
Tournaments,
Sports Events,
trending,
sub categories - Viral News
Social Media Trends
Memes & Challenges
Internet Sensations
Public Buzz
entertainment,
sub categories - Movies,
Music,
Celebrity News,
OTT / Streaming,
TV Shows,

lifestyle & society 
sub categories - Health & Wellness
Mental Health
Food & Nutrition
Travel
Fashion
Fitness
JSON FORMAT:
{{
  "headline": "",
  "news": "",
  "notification": "",
  "categories": ""
}}

Title: {title}
Article: {article}
"""

# ---------------- HELPERS ----------------
def article_text(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        art = soup.find("article")
        if art:
            return art.get_text(" ", strip=True)
    except:
        pass
    return ""


def already_exists(link):
    res = supabase.table("news").select("id").eq("link", link).execute()
    return bool(res.data)


def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# ---------------- API ----------------
@app.get("/news")
def fetch_news():
    inserted = []
    errors = []

    for rss in RSS_URLS:
        feed = feedparser.parse(rss)

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

                prompt = PROMPT.format(
                    title=entry.title,
                    article=article
                )

                response = model.generate_content(prompt)
                ai_json = clean_json(response.text)

                image = ""
                if hasattr(entry, "media_thumbnail"):
                    image = entry.media_thumbnail[0].get("url", "")

                data = {
                    "headline": ai_json["headline"],
                    "news": ai_json["news"],
                    "notification": ai_json["notification"],
                    "categories": ai_json["categories"],
                    "link": entry.link,
                    "image": image,
                    "original":entry.title,
                    "published_date": pub_date.isoformat(),
                }

                supabase.table("news").insert(data).execute()
                inserted.append(ai_json["headline"])

            except Exception as e:
                errors.append(str(e))

    return {
        "inserted_count": len(inserted),
        "headlines": inserted,
        "errors": errors
    }

class GetNews(BaseModel):
    email: str

@app.post("/get_news")
def get_news(user: GetNews):

    email = user.email
    final_news = []

    # 1️⃣ Get user suggestions
    user_res = supabase.table("users") \
        .select("suggestions") \
        .eq("email", email) \
        .execute()

    if not user_res.data:
        return {"news": []}

    suggestions = user_res.data[0]["suggestions"] or []

    # make all suggestions lowercase
    suggestions = [s.lower() for s in suggestions]

    # 2️⃣ Get all news
    news_res = supabase.table("news") \
        .select("*") \
        .order("published_date", desc=True) \
        .execute()

    # 3️⃣ Match suggestions with categories
    for row in news_res.data:

        categories = (row.get("categories") or "").lower()

        for sug in suggestions:
            if sug in categories:
                final_news.append({
                    "headline": row["headline"],
                    "news": row["news"],
                    "notification": row["notification"],
                    "categories": row["categories"],
                    "link": row["link"],
                    "image": row["image"],
                    "published_date": row["published_date"]
                })
                break  # avoid duplicate same news

        if len(final_news) >= 5:
            break

    return {"news": final_news}
