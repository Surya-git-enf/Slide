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
model = genai.GenerativeModel("models/gemini-1.5-flash")

# ---------------- RSS ----------------
RSS_URLS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]

# ---------------- PROMPT ----------------
PROMPT = """
You are a professional news writer.

Rewrite the news clearly and attractively.

Rules:
- Output ONLY valid JSON
- 2–3 short paragraphs for news
- Categories = sub-category first, then main category

Main categories:
global / general,
business and finance,
science and technology,
sports,
trending,
entertainment,
lifestyle

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

# ---------------- FUNCTIONS ----------------
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


def clean_ai_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# ---------------- API ----------------
@app.get("/news")
def fetch_news():
    saved = 0

    for rss in RSS_URLS:
        feed = feedparser.parse(rss)

        for entry in feed.entries[:10]:
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

            try:
                response = model.generate_content(prompt)
                ai_json = clean_ai_json(response.text)
            except Exception as e:
                print("❌ AI error:", e)
                continue

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
                "published_date": pub_date.isoformat(),
            }

            supabase.table("news").insert(data).execute()
            saved += 1

    return {"status": "done", "saved": saved}
