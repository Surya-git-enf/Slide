# main.py
import os
import time
import re
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from supabase import create_client
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any

# ---------------- CONFIG (use environment variables) ----------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash")  # default model id
RSS_URLS = os.getenv("RSS_URLS", "https://feeds.bbci.co.uk/news/world/rss.xml").split(",")

if not (SUPABASE_URL and SUPABASE_KEY and GEMINI_API_KEY):
    raise RuntimeError("Set SUPABASE_URL, SUPABASE_KEY and GEMINI_API_KEY environment variables before running")

# ---------------- SUPABASE CLIENT ----------------
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- FASTAPI APP ----------------
app = FastAPI(title="Simple AI News Service")

# ---------------- HELPERS ----------------


def get_article_text(url: str) -> str:
    """Fetch page and try to extract main article text (simple heuristics)."""
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Try <article>
    article = soup.find("article")
    if article:
        return article.get_text("\n\n", strip=True)

    # Fallback selectors
    selectors = [
        'div[itemprop="articleBody"]',
        'div[class*="article"]',
        'div[class*="story"]',
        'main'
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            return node.get_text("\n\n", strip=True)

    # Last fallback: page body text (trimmed)
    body = soup.body
    if body:
        return body.get_text("\n\n", strip=True)[:3000]

    return ""


def call_gemini_rest(prompt: str) -> str:
    """
    Call Gemini REST generateContent endpoint using the API key.
    Returns the text output (string).
    """
    endpoint = f"https://generativelanguage.googleapis.com/v1/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "temperature": 0.2,
        "maxOutputTokens": 700
    }

    r = requests.post(endpoint, json=payload, timeout=30)
    r.raise_for_status()
    j = r.json()

    # Try common response shapes for model text
    # 1) candidates -> content -> parts -> text
    try:
        cands = j.get("candidates")
        if cands and isinstance(cands, list):
            first = cands[0]
            content = first.get("content", {})
            parts = content.get("parts")
            if parts:
                return "".join(p.get("text", "") for p in parts)
            # fallback older shape
            if isinstance(first.get("content"), dict) and first["content"].get("text"):
                return first["content"]["text"]
    except Exception:
        pass

    # 2) output blocks
    try:
        outputs = j.get("output", [])
        for out in outputs:
            cont = out.get("content", [])
            for c in cont:
                if isinstance(c, dict) and "text" in c:
                    return c["text"]
    except Exception:
        pass

    # 3) as last resort return whole json as string so we can debug
    return json.dumps(j)


def extract_json_from_text(text: str) -> Dict[str, Any]:
    """
    Find the first JSON object in text and parse it.
    Raises ValueError if none found or invalid JSON.
    """
    # First try a recursive-safe regex pattern (may not work in all engines)
    m = re.search(r"\{(?:[^{}]|(?R))*\}", text, re.S)
    if not m:
        # simpler fallback
        m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("No JSON object found in AI output")

    js = m.group(0)
    # quick cleanups (remove trailing commas)
    js = re.sub(r",\s*}", "}", js)
    js = re.sub(r",\s*\]", "]", js)
    return json.loads(js)


def already_exists(link: str) -> bool:
    """Check if a news row with the same link already exists (simple dedupe)."""
    try:
        res = supabase.table("news").select("id").eq("link", link).limit(1).execute()
        data = res.get("data") or []
        return len(data) > 0
    except Exception:
        return False


# ---------------- PROMPT (strict JSON output) ----------------
PROMPT_TEMPLATE = """
You are a world-class professional news editor and journalist.

TASK:
Rewrite the article into a short, high-quality, engaging piece and produce metadata.

OUTPUT RULES (MANDATORY):
- Output ONLY valid JSON (no extra text).
- JSON keys must be exactly: headline, news, notification, categories

JSON FORMAT:
{
  "headline": "string",         // 1-2 lines, catchy
  "news": "string",             // 2-3 short paragraphs separated by \\n\\n
  "notification": "string",     // one alert-style sentence
  "categories": "string"        // comma-separated, lowercase (subcategory first, main category last)
}

INPUT:
Original title: {original_title}
Article text: {article_text}
Source link: {link}
"""

# ---------------- CORE PROCESSING ----------------


def process_entry(entry) -> bool:
    """
    Process one RSS entry:
    - Skip if older than 3 days
    - Skip if duplicate
    - Scrape article
    - Call Gemini (REST) with strict JSON prompt
    - Parse JSON and insert into Supabase 'news' table
    Returns True if inserted.
    """
    if not hasattr(entry, "published_parsed"):
        return False

    pub_dt = datetime(*entry.published_parsed[:6])
    if (datetime.now() - pub_dt).days > 3:
        return False

    if already_exists(entry.link):
        return False

    article_text = get_article_text(entry.link)
    if not article_text:
        return False

    prompt = PROMPT_TEMPLATE.format(
        original_title=entry.title,
        article_text=article_text[:3000],  # limit prompt length
        link=entry.link
    )

    try:
        ai_raw = call_gemini_rest(prompt)
    except Exception as e:
        print("AI request failed:", e)
        return False

    try:
        parsed = extract_json_from_text(ai_raw)
    except Exception as e:
        print("AI parse failed:", e)
        print("AI raw (truncated):", ai_raw[:800])
        return False

    # validate keys
    for k in ("headline", "news", "notification", "categories"):
        if k not in parsed:
            print("Missing required key from AI output:", k)
            return False

    # image detection: try feed media then og:image
    image_url = ""
    try:
        if hasattr(entry, "media_thumbnail"):
            image_url = entry.media_thumbnail[0].get("url", "") if entry.media_thumbnail else ""
    except Exception:
        image_url = ""

    if not image_url:
        try:
            r = requests.get(entry.link, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            m = re.search(r'<meta property="og:image" content="([^"]+)"', r.text)
            if m:
                image_url = m.group(1)
        except Exception:
            image_url = ""

    record = {
        "headline": parsed["headline"],
        "news": parsed["news"],
        "notification": parsed["notification"],
        "categories": parsed["categories"],
        "link": entry.link,
        "image": image_url,
        "original_title": entry.title,
        "published_date": pub_dt.isoformat()
    }

    try:
        res = supabase.table("news").insert(record).execute()
        if res.get("error"):
            print("Supabase insert error:", res["error"])
            return False
    except Exception as e:
        print("Supabase exception:", e)
        return False

    print("Inserted:", parsed["headline"])
    return True


def run_once(max_per_feed: int = 10):
    """Run the pipeline once over all RSS feeds (use in cron/Render job)."""
    for rss in RSS_URLS:
        print("Fetching:", rss)
        try:
            feed = feedparser.parse(rss)
        except Exception as e:
            print("Feed parse error:", e)
            continue

        processed = 0
        for entry in feed.entries:
            if processed >= max_per_feed:
                break
            try:
                if process_entry(entry):
                    processed += 1
            except Exception as e:
                print("Entry process error:", e)
        print(f"Processed {processed} items from {rss}")


# ---------------- FASTAPI endpoint: get news by user's suggestions ----------------


class EmailRequest(BaseModel):
    email: str


@app.post("/get_news")
def get_news(req: EmailRequest):
    """
    Get user's suggestions (array) from 'user' table (column name: suggestions),
    return up to 5 news rows that match any suggestion token in news.categories.
    """
    try:
        r = supabase.table("user").select("suggestions").eq("email", req.email).limit(1).execute()
    except Exception:
        return {"news": []}

    data = r.get("data") or []
    if not data:
        return {"news": []}

    suggestions = data[0].get("suggestions") or []
    if isinstance(suggestions, str):
        try:
            suggestions = json.loads(suggestions)
        except Exception:
            suggestions = [suggestions]

    suggestions = [s.lower() for s in suggestions]

    news_resp = supabase.table("news").select("*").order("published_date", desc=True).limit(500).execute()
    news_rows = news_resp.get("data") or []

    matched = []
    for row in news_rows:
        cats = (row.get("categories") or "").lower()
        for sug in suggestions:
            token_parts = re.sub(r"[^a-z0-9 ]", " ", sug).split()
            if any(part and part in cats for part in token_parts):
                matched.append(row)
                break
        if len(matched) >= 5:
            break

    return {"news": matched}


# ---------------- RUN (CLI) ----------------
if __name__ == "__main__":
    # Simple: run once now. For production, schedule this via cron or a background worker.
    print("Running one pass over RSS feeds...")
    run_once(max_per_feed=10)
    print("Done.")
