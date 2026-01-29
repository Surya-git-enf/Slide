import gspread
import os
from google.oauth2.service_account import Credentials
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
import time
import json
import google.generativeai as genai

# ---------------- CONFIG ---------------- #

RSS_URLS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
  
  "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
  "https://moxie.foxnews.com/feedburner/latest.xml",
  "https://feeds.npr.org/1001/rss.xml",
  "https://rss.usatoday.com/rss/TopStories/usatoday-topstories.xml",
  "https://techcrunch.com/feed/",
  "https://www.wired.com/feed/rss",
  "https://www.theverge.com/rss/index.xml",
  "https://www.cnet.com/rss/news/",
  "https://www.engadget.com/rss.xml",
  "https://venturebeat.com/feed/",
  "https://feeds.arstechnica.com/arstechnica/technology-lab",
  "http://www.espncricinfo.com/rss/content/story/feeds/0.xml",
  "http://feeds.bbci.co.uk/sport/cricket/rss.xml"
]

SHEET_KEY = "1ahwKkDMSm_o-T17xp4CMe7M1tzR6XgRSz0UHcTiFEzE"

# ---------------- GOOGLE SHEETS ---------------- #

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_KEY).sheet1

# ---------------- GEMINI ---------------- #
api = os.getenv("GEMINI_API_KET")
model = os.getenv("GEMINI_MODEL")
genai.configure(api_key=api)
model = genai.GenerativeModel(model)

# ---------------- LOOP ---------------- #

while True:
    print("🔄 Fetching RSS feeds...")

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:10]:

            # ---- Date filter (last 3 days) ---- #
            if not hasattr(entry, "published_parsed"):
                continue

            pub_date = datetime(*entry.published_parsed[:6])
            if (datetime.now() - pub_date).days > 3:
                continue

            # ---- Scrape article ---- #
            try:
                html = requests.get(entry.link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                soup = BeautifulSoup(html.text, "html.parser")
                article = soup.find("article")
                if not article:
                    continue
                art = article.get_text(" ", strip=True)
            except:
                continue

            # ---- Gemini Prompt ---- #
            prompt = f"""
You are a professional world-class news editor and journalist.

Your task is to rewrite the given news into a short, high-quality, engaging news article suitable for a news app.

STRICT OUTPUT RULES (MANDATORY):
- Output ONLY valid JSON
- Do NOT add explanations
- Do NOT add markdown
- Do NOT add comments
- Do NOT add any extra text before or after JSON
- JSON keys must be exactly as specified

JSON FORMAT (EXACT):
{
  "headline": "",
  "news": "",
  "notification": "",
  "categories": ""
}

CONTENT RULES:

1. headline
- 1 to 2 lines maximum
- Must be catchy, curious, and engaging
- Should feel like breaking or important news
- Do NOT repeat the original headline word-for-word

2. news
- Rewrite the article in your own words
- Remove all unnecessary or junk information
- 2 to 3 short paragraphs ONLY
- Each paragraph must be separated by "\n\n"
- The last paragraph must NOT end with "\n\n"
- Clear, factual, and easy to read
- Sound more interesting than the original article

3. notification
- One short alert-style sentence
- Suitable for push notification
- Clear and impactful

4. categories
- All lowercase
- Comma-separated
- Sub-categories FIRST, main category LAST
- Examples:
  - "politics, international relations, general / global"
  - "artificial intelligence, startups, science & technology"
  - "climate change, natural disasters, environment"
- Do NOT use symbols like # or /

SOURCE INFORMATION (USE THIS CONTENT ONLY):

Original headline:
{entry.title}

Full article content:
{art}

Source link:
{entry.link}

FINAL REMINDER:
Return ONLY the JSON object.
If the output is not valid JSON, it is WRONG.
"""

            try:
                response = model.generate_content(prompt)
                data = json.loads(response.text)
            except:
                print("❌ Gemini or JSON error")
                continue

            # ---- Append to Sheet ---- #
            sheet.append_row([
                data["headline"],
                data["news"],
                data["notification"],
                data["categories"],
                entry.link,
                entry.get("media_thumbnail", [{}])[0].get("url", ""),
                entry.title,
                pub_date.strftime("%Y-%m-%d %H:%M:%S")
            ])

            print("✅ Added:", data["headline"])

    print("⏳ Sleeping 30 minutes...\n")
    time.sleep(1800)  # 30 minutes
