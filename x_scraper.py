"""
scrape_citimmcanada.py
======================
Scrapes up to 20 recent posts from @CitImmCanada on X (Twitter) without
logging in using a Playwright headless browser.

Dependencies:
  pip install requests beautifulsoup4 playwright
  python -m playwright install chromium
"""

import sys

from bs4 import BeautifulSoup

import json

import re

from pathlib import Path

from datetime import datetime

from datetime import date

from zoneinfo import ZoneInfo

SCREEN_NAME = "CitImmCanada"
MAX_POSTS = 20

def _try_playwright() -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return []

    posts = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()
        try:
            page.goto(
                f"https://x.com/{SCREEN_NAME}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )

            try:
                page.wait_for_selector('[data-testid="tweet"]', timeout=20_000)
            except PWTimeout:
                pass

            for _ in range(4):
                page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                page.wait_for_timeout(2_000)

                show_more_sel = '[data-testid="tweet-text-show-more-link"]'
                for btn in page.query_selector_all(show_more_sel):
                    try:
                        btn.scroll_into_view_if_needed()
                        btn.click()
                        page.wait_for_timeout(600)
                    except Exception:
                        pass

            # ---- TEMP DEBUG: delete these 3 lines once the cause is confirmed ----
            page.screenshot(path="debug_screenshot.png", full_page=True)
            print(f"DEBUG title: {page.title()}", file=sys.stderr)
            # ------------------------------------------------------------------------

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            tweet_els = soup.select('[data-testid="tweet"]')
            print(f"DEBUG tweets found: {len(tweet_els)}", file=sys.stderr)  # TEMP DEBUG

            for tweet_el in tweet_els:
                text_el = tweet_el.select_one('[data-testid="tweetText"]')
                time_el = tweet_el.select_one("time")
                link_el = tweet_el.select_one('a[href*="/status/"]')

                if text_el:
                    for a_tag in text_el.find_all('a'):
                        if a_tag.get('href', '').startswith('http'):
                            joined = a_tag.get_text('', strip=True).rstrip('…')
                            a_tag.clear()
                            a_tag.append(joined)

                text = text_el.get_text(" ", strip=True) if text_el else ""

                date_str = time_el.get("datetime", "") if time_el else ""
                tweet_url = "https://x.com" + link_el["href"] if link_el else ""
                posts.append({"text": text, "date": date_str, "url": tweet_url})
                if len(posts) >= MAX_POSTS:
                    break

        except PWTimeout:
            pass
        except Exception:
            pass
        finally:
            browser.close()

    return posts


def main() -> None:
    script_dir = Path(__file__).parent
    output_path = "x_posts.json"

    # Load existing posts
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            existing_posts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_posts = []

    existing_urls = {p.get("url") for p in existing_posts}

    # Only keep new posts whose URL isn't already saved
    new_posts = _try_playwright()
    unique_new = [p for p in new_posts if p.get("url") not in existing_urls]

    combined = existing_posts + unique_new
    combined.sort(key=lambda p: p.get("date", ""), reverse=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()