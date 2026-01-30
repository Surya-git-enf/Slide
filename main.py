import time
import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from supabase import create_client
import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI


# ----------------------------------
# Load environment variables
# ----------------------------------
load_dotenv()
app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
# ----------------------------------
# Supabase client
# ----------------------------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------------------------
# Gemini AI setup
# ----------------------------------
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

# ----------------------------------
# RSS feed URLs (add more if you want)
# ----------------------------------
RSS_URLS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
]

# ----------------------------------
# AI PROMPT (STRICT JSON)
# ----------------------------------
PROMPT_TEMPLATE = """
You are a professional news writer and editor.

Your task:
Rewrite the given news article into a high-quality, short, engaging news post.

Rules:
1. Write a curious and informative headline (1–2 lines).
2. Write the news in 2 or 3 short paragraphs.
   - Improve clarity and quality.
   - Remove junk and repetition.
   - Keep it factual and engaging.
3. Write one short notification line that creates curiosity.
4. Choose categories using the rules below.

Categories rules (VERY IMPORTANT):
- Use ONLY these main categories:
  global / general
  business and finance
  science and technology
  sports
  trending
  entertainment
  lifestyle

- Always write:
  sub-category first, main category second
- Use lowercase only
- Comma separated
- Multiple categories allowed

Examples:
- crypto, business and finance
- football, sports
- world news, global / general
- movies, entertainment

Output format:
Return ONLY a valid JSON object.
No explanations.
No markdown.
No extra text.

JSON format:
{
  "headline": "...",
  "news": "paragraph 1\n\nparagraph 2",
  "notification": "...",
  "categories": "sub-category, main category"
}

Input data:
Headline: {{ORIGINAL_HEADLINE}}
Article: {{ARTICLE_TEXT}}
Link: {{NEWS_LINK}}
"""

# ----------------------------------
# Run for 30 minutes total
# ----------------------------------
START_TIME = time.time()
RUN_DURATION = 30 * 60      # 30 minutes
SLEEP_TIME = 30 * 60        # run every 30 minutes

while time.time() - START_TIME < RUN_DURATION:

    print("🔄 Fetching RSS feeds...")

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:

            # ----------------------------------
            # Publish date check (last 3 days)
            # ----------------------------------
            if not hasattr(entry, "published_parsed"):
                continue

            pub_date = datetime(*entry.published_parsed[:6])
            if (datetime.now() - pub_date).days > 3:
                continue

            # ----------------------------------
            # Fetch article HTML
            # ----------------------------------
            response = requests.get(
                entry.link,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            soup = BeautifulSoup(response.text, "html.parser")
            article = soup.find("article")

            if not article:
                continue

            article_text = article.get_text(" ", strip=True)

            # ----------------------------------
            # Ask Gemini AI
            # ----------------------------------
            prompt = PROMPT_TEMPLATE.format(
                title=entry.title,
                content=article_text
            )

            ai_response = model.generate_content(prompt)

            try:
                ai_data = json.loads(ai_response.text)
            except:
                print("❌ Invalid JSON from AI")
                continue

            # ----------------------------------
            # Insert into Supabase
            # ----------------------------------
            supabase.table("news").insert({
                "headline": ai_data["headline"],
                "news": ai_data["news"],
                "notification": ai_data["notification"],
                "categories": ai_data["categories"],
                "link": entry.link,
                "image": "",   # RSS usually has no image
                "original": entry.title,
                "published_date": pub_date.strftime("%Y-%m-%d %H:%M:%S")
            }).execute()

            print("✅ News saved:", ai_data["headline"])

    print("😴 Sleeping for 30 minutes...")
    time.sleep(SLEEP_TIME)

print("⏹ Finished 30 minutes run")
