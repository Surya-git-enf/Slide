import time
import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv
from fastapi import FastAPI

# New GenAI import
from google import genai
from google.genai import types, errors

# ----------------------------------
# Load environment variables
# ----------------------------------
load_dotenv()
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "good"}

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.1")  # fallback model id if not set

# ----------------------------------
# Supabase client
# ----------------------------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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
{{
  "headline": "...",
  "news": "paragraph 1\\n\\nparagraph 2",
  "notification": "...",
  "categories": "sub-category, main category"
}}

Input data:
Headline: {orig_headline}
Article: {article_text}
Link: {news_link}
"""

# ----------------------------------
# Run for 30 minutes total
# ----------------------------------
START_TIME = time.time()
RUN_DURATION = 30 * 10      # 30 minutes
SLEEP_TIME = 30 * 60        # run every 30 minutes

# Create genai client (Gemini Developer API mode using API key)
# The client will pick GEMINI_API_KEY from argument or environment automatically.
client = genai.Client(api_key=GEMINI_API_KEY)

try:
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
                try:
                    response = requests.get(
                        entry.link,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15
                    )
                except Exception as e:
                    print("⚠️ Failed to fetch article:", e)
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                article = soup.find("article")
                # fallback: if <article> missing, try main content heuristics
                if not article:
                    # try common containers
                    possible = soup.find_all(["main", "div"], class_=lambda x: x and "content" in x.lower())
                    article = possible[0] if possible else None
                if not article:
                    continue

                article_text = article.get_text(" ", strip=True)

                # Construct prompt safely (use .format with named fields)
                prompt = PROMPT_TEMPLATE.format(
                    orig_headline=entry.title,
                    article_text=article_text,
                    news_link=entry.link
                )

                # ----------------------------------
                # Ask Gemini AI (via google-genai client)
                # ----------------------------------
                try:
                    # simple text request — returns a response object with .text and .parts
                    resp = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        # Optional: enforce JSON response mime type or other generation config
                        # config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                except errors.APIError as e:
                    print("❌ API error from Gen AI:", e)
                    continue
                except Exception as e:
                    print("❌ Unknown error calling GenAI:", e)
                    continue

                ai_text = getattr(resp, "text", None)
                # sometimes response may be split into parts; join if needed
                if not ai_text:
                    parts = []
                    for p in getattr(resp, "parts", []) or []:
                        if p.text:
                            parts.append(p.text)
                        elif p.inline_data:
                            parts.append(p.inline_data.decode() if isinstance(p.inline_data, bytes) else str(p.inline_data))
                    ai_text = "\n".join(parts).strip()

                if not ai_text:
                    print("❌ Empty response from AI")
                    continue

                # ----------------------------------
                # Parse AI JSON output
                # ----------------------------------
                try:
                    ai_data = json.loads(ai_text)
                except Exception:
                    # If model returned extra text or log, try to extract the JSON substring
                    try:
                        start = ai_text.index("{")
                        end = ai_text.rindex("}") + 1
                        ai_data = json.loads(ai_text[start:end])
                    except Exception:
                        print("❌ Invalid JSON from AI — skipping. Raw:", ai_text[:300])
                        continue

                # minimal validation:
                if not all(k in ai_data for k in ("headline", "news", "notification", "categories")):
                    print("❌ AI JSON missing required keys:", ai_data.keys())
                    continue

                # ----------------------------------
                # Optional: avoid duplicates in Supabase (check by link or headline)
                # ----------------------------------
                try:
                    exists = supabase.table("news").select("id").eq("link", entry.link).execute()
                    if exists.data and len(exists.data) > 0:
                        print("🔁 Already saved:", entry.link)
                        continue
                except Exception:
                    # if check fails, proceed to insert to avoid data loss
                    pass

                # ----------------------------------
                # Insert into Supabase
                # ----------------------------------
                try:
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
                except Exception as e:
                    print("❌ Failed to insert into Supabase:", e)

        print("😴 Sleeping for 30 minutes...")
        time.sleep(SLEEP_TIME)

finally:
    # ensure client closes resources
    try:
        client.close()
    except Exception:
        pass

print("⏹ Finished 30 minutes run")
