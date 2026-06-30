"""
Scrape Program Delivery Update (PDU) links from the IRCC updates page,
then visit each of the 20 PDU pages and extract:
  - code          : reference code, e.g. "(REV-OVS-6-11)", if present
  - description   : all content between the date and the first instruction heading
  - instructions  : updated / new / deleted instruction links or titles
  - date_modified : ISO date string from page metadata

Usage:
    python get_pdu_links.py

Dependencies:
    pip install requests beautifulsoup4

Output:
    pdu_details.json  –  JSON file with a list of 20 enriched dicts.
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup, NavigableString

from datetime import date

import os
from google import genai

BASE_URL = "https://www.canada.ca"
UPDATES_URL = (
    f"{BASE_URL}/en/immigration-refugees-citizenship/corporate/"
    "publications-manuals/operational-bulletins-manuals/updates.html"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PDU-scraper/1.0; "
        "+https://www.canada.ca)"
    )
}

ROWS_PER_PAGE = 20
OUTPUT_FILE = "pdu_details.json"

# Google Doc shared as "Anyone with the link" / "Editor" – its plain text
# is appended to the end of the script's output.
GOOGLE_DOC_URL = "https://docs.google.com/document/d/10JIbTuk_dtZ4swG95srMYO-mtyzjplLgkkzCVu9AnnQ/edit?usp=sharing"

# <h2> text values that mark the start of instructions or end of body.
STOP_HEADINGS = {
    "updated instructions",
    "new instructions",
    "deleted instructions",
    "page details",
}


# Matches a PDU reference code like "(REV-OVS-6-11)".
CODE_RE = re.compile(r"^\([A-Z]+-[A-Z0-9]+-[\dA-Z]+-[\dA-Z]+\)$", re.IGNORECASE)

# Fixed disclaimer present on every PDU page – skip it.
DISCLAIMER_FRAGMENT = "This section contains policy"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> BeautifulSoup:
    """Download *url* and return a BeautifulSoup parse tree."""
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def make_absolute(href: str) -> str:
    """Return an absolute URL, prepending BASE_URL for relative paths."""
    return BASE_URL + href if href.startswith("/") else href


def element_to_text(el) -> str:
    """
    Render an element's content as plain text, converting <a> tags to
    Markdown-style [link text](url) so hyperlinks in the description
    are preserved.
    """
    parts = []
    for node in el.children:
        if isinstance(node, NavigableString):
            parts.append(str(node))
        elif node.name == "a":
            href = make_absolute(node.get("href", ""))
            text = node.get_text(strip=True)
            parts.append(f"[{text}]({href})")
        else:
            # Recurse into any other inline tag (strong, em, span, …).
            parts.append(element_to_text(node))
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Index-page scraping
# ---------------------------------------------------------------------------

def extract_pdu_links(soup: BeautifulSoup) -> list[dict]:
    """
    Return every PDU link found inside the updates table.

    Each element is a dict with keys:
        title  – visible link text
        url    – absolute URL of the PDU page
        date   – publication date string (from the index table)
    """
    table = soup.find("table")
    if table is None:
        raise RuntimeError("Could not find a <table> on the page.")

    links = []
    for row in table.find_all("tr"):
        anchor = row.find("a", href=True)
        if anchor is None:
            continue  # header row or rows without links

        href = make_absolute(anchor["href"])
        cells = row.find_all("td")
        date = cells[-1].get_text(strip=True) if cells else ""
        links.append({"title": anchor.get_text(strip=True), "url": href, "date": date})

    return links


# ---------------------------------------------------------------------------
# Detail-page scraping
# ---------------------------------------------------------------------------

def _get_date_modified(soup: BeautifulSoup) -> str:
    """Extract the date-modified from <meta name="dcterms.modified">."""
    meta = soup.find("meta", {"name": "dcterms.modified"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    time_tag = soup.find("time")
    if time_tag:
        return time_tag.get("datetime", time_tag.get_text(strip=True)).strip()
    return ""


def _get_code(main) -> str:
    """
    Look for an <h2> whose full text matches the reference-code pattern,
    e.g. "(REV-OVS-6-11)".  Returns the raw text or "".
    """
    for h2 in main.find_all("h2"):
        text = h2.get_text(strip=True)
        if CODE_RE.match(text):
            return text
    return ""


def _find_date_paragraph(main):
    """
    Return the <p> that contains the publication date by using document
    structure rather than text patterns, so it never returns None.

    The date paragraph is always the first <p> that follows a known
    structural anchor:
      1. The code <h2>  (e.g. "(REV-OVS-6-11)") when a code is present.
      2. The disclaimer <p> ("This section contains policy…") otherwise.

    Falling back to the first <p> in main ensures a non-None result even
    on pages with unexpected markup.
    """
    # Case 1: a code heading is present — date <p> is the next <p> in
    # document order after the code <h2>, regardless of nesting depth.
    for h2 in main.find_all("h2"):
        if CODE_RE.match(h2.get_text(strip=True)):
            result = h2.find_next("p")
            if result:
                return result
            break   # code found but no following <p>; fall through

    # Case 2: no code — date <p> is the next <p> in document order after
    # the disclaimer, regardless of nesting depth.
    for p in main.find_all("p"):
        if DISCLAIMER_FRAGMENT in p.get_text():
            result = p.find_next("p")
            if result:
                return result
            break   # disclaimer found but no following <p>; fall through

    # Absolute fallback: first <p> in main.
    return main.find("p")


def _get_description(main) -> str:
    """
    Collect all content between the date paragraph and the first
    instruction / 'Page details' <h2>.

    Handles mixed content: plain <p> tags, bulleted <ul> lists, and
    paragraphs whose bullets contain hyperlinks (rendered as Markdown links).
    """
    date_para = _find_date_paragraph(main)

    parts = []

    for el in date_para.find_all_next(True):
        # Stop when we reach the instruction section or page footer.
        if el.name == "h2" and el.get_text(strip=True).lower() in STOP_HEADINGS:
            break

        # Top-level <p> elements (skip ones nested inside lists).
        if el.name == "p":
            if el.find_parent(["ul", "li"]):
                continue
            text = element_to_text(el)
            if text and DISCLAIMER_FRAGMENT not in text:
                parts.append(text)

        # Top-level <ul> elements: extract each <li> with its links.
        elif el.name == "ul":
            if el.find_parent("ul"):
                continue  # skip nested lists
            for li in el.find_all("li", recursive=False):
                text = element_to_text(li)
                if text:
                    parts.append(text)

    return "\n".join(parts)


def _get_instructions(main) -> dict:
    """
    Scan <h2> headings for "Updated instructions", "New instructions",
    and "Deleted instructions".  The sibling <ul> holds the items:
      - Updated / New  → {"text": ..., "url": ...} for each <li><a>
      - Deleted        → plain title strings (no links)

    Returns a dict with only the keys that are present on the page.
    """
    instructions: dict[str, list] = {}

    for h2 in main.find_all("h2"):
        heading_text = h2.get_text(strip=True).lower()
        if heading_text not in STOP_HEADINGS - {"page details"}:
            continue

        # Walk forward siblings until we find the accompanying <ul>.
        ul = None
        for sibling in h2.find_next_siblings():
            if sibling.name == "ul":
                ul = sibling
                break
            if sibling.name in ("h2", "h3"):
                break

        if ul is None:
            continue

        items = []
        for li in ul.find_all("li"):
            anchor = li.find("a", href=True)
            if anchor:
                items.append(
                    {
                        "text": anchor.get_text(strip=True),
                        "url": make_absolute(anchor["href"]),
                    }
                )
            else:
                plain = li.get_text(strip=True)
                if plain:
                    items.append(plain)

        if items:
            # Key is the first word: "updated", "new", or "deleted".
            key = heading_text.split()[0]
            instructions[key] = items

    return instructions


def fetch_pdu_details(url: str) -> dict:
    """
    Visit a single PDU page and return a dict with:
        code          – reference code or ""
        description   – all text between the date and first instruction heading
        instructions  – {"updated": [...], "new": [...], "deleted": [...]}
        date_modified – ISO date string
    """
    soup = fetch_page(url)
    main = soup.find("main") or soup

    return {
        "code": _get_code(main),
        "description": _get_description(main),
        "instructions": _get_instructions(main),
        "date_modified": _get_date_modified(soup),
    }

# ---------------------------------------------------------------------------
# Google Doc scraping
# ---------------------------------------------------------------------------
def _extract_doc_id(doc_url: str) -> str:
    """Pull the document ID out of any standard Google Docs URL."""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", doc_url)
    if not match:
        raise ValueError(f"Could not find a document ID in: {doc_url}")
    return match.group(1)
def fetch_google_doc_text(doc_url: str) -> str:
    """
    Download the plain-text content of a Google Doc that is shared as
    "Anyone with the link". Uses the document's built-in export endpoint,
    so no OAuth credentials are required.
    """
    doc_id = _extract_doc_id(doc_url)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    response = requests.get(export_url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text.strip()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Fetch the index page and collect the first 20 PDU entries.
    soup = fetch_page(UPDATES_URL)
    all_links = extract_pdu_links(soup)
    top_20 = all_links[:ROWS_PER_PAGE]

    # 2. Visit each PDU page and merge its details into the entry dict.
    results = []
    for i, entry in enumerate(top_20, start=1):
        #print(f"[{i:02}/{len(top_20)}] {entry['url']}")
        try:
            details = fetch_pdu_details(entry["url"])
        except Exception as exc:
            print(f"         ERROR: {exc}")
            details = {
                "code": "",
                "description": "",
                "instructions": {},
                "date_modified": "",
            }

        result = {**entry, **details}
        if (date.today() - date.fromisoformat(result["date_modified"])).days <= 7:
            results.append(result)

        # Be polite to the server.
        if i < len(top_20):
            time.sleep(0.5)

    # 3. Write output.
    #with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        #json.dump(results, f, ensure_ascii=False, indent=2)

    #print(f"\nSaved {len(results)} entries to {OUTPUT_FILE}")

    output = "PDU\n\n"
    for i in results:
        output += "URL: " + i["url"] + "\n"
        output += "Title: " + i["title"] + "\n"
        if i["code"] != "":
            output += "Code: " + i["code"] + "\n"
        output += "Date Created: " + i["date"] + "\n"
        output += "Description: " + i["description"] + "\n"
        if "new" in i["instructions"]:
            output += "New instructions:\n"
            for j in range(len(i["instructions"]["new"])):
                output += "New instruction " + str(j + 1) + " Text: " + i["instructions"]["new"][j]["text"] + "\n"
                output += "New instruction " + str(j + 1) + " URL: " + i["instructions"]["new"][j]["url"] + "\n"
        if "updated" in i["instructions"]:
            output += "Updated instructions:\n"
            for j in range(len(i["instructions"]["updated"])):
                output += "Updated instruction " + str(j + 1) + " Text: " + i["instructions"]["updated"][j]["text"] + "\n"
                output += "Updated instruction " + str(j + 1) + " URL: " + i["instructions"]["updated"][j]["url"] + "\n"
        if "deleted" in i["instructions"]:
            output += "Deleted instructions:\n"
            for j in range(len(i["instructions"]["deleted"])):
                output += "Deleted instruction " + str(j + 1) + ": " + i["instructions"]["deleted"][j] + "\n"
        output += "Date modified: " + date.fromisoformat(i["date_modified"]).strftime("%B %d, %Y") + "\n\n"

    # 4. Append the contents of the linked Google Doc.
    try:
        output += "X\n\n" + fetch_google_doc_text(GOOGLE_DOC_URL)
    except Exception as exc:
        print(f"         ERROR fetching Google Doc: {exc}")
        
    #client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    #response = client.models.generate_content(
    #model="gemini-2.5-flash",
    #contents="hello"
    #)

    #print(response.text)

    print(output)

if __name__ == "__main__":
    main()