import re
import unicodedata
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_TIMEOUT = 12
MAX_WEBSITE_CHARS = 6000

NOISE_SECTION_MARKERS = [
    "NOS AUTRES ADRESSES",
    "NOS ADRESSES",
    "AUTRES ADRESSES",
    "OUR OTHER LOCATIONS",
    "OTHER LOCATIONS",
    "OUR RESTAURANTS",
    "NOS RESTAURANTS",
    "DÉCOUVRIR NOS RESTAURANTS",
    "DECOUVRIR NOS RESTAURANTS",
    "FRENCH BISTRO & BAR",
    "COMFORT FOOD & EXCITING DRINKS",
]

ADDRESS_PATTERN = re.compile(
    r"\b\d{1,4}\s*(?:bis|ter)?\s*,?\s+"
    r"(?:rue|avenue|av\.?|boulevard|bd|quai|place|passage|impasse|allée|allee|cours|route|square|villa|cité|cite)"
    r"\s+.{3,80}?(?=\s+\d{5}\b|,|\n|$)",
    re.IGNORECASE,
)


def normalize_for_compare(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_probably_valid_url(url: str) -> bool:
    if not url:
        return False

    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def extract_address_key(text: str) -> str:
    match = ADDRESS_PATTERN.search(text or "")
    if not match:
        return ""

    return normalize_for_compare(match.group(0))


def same_address(candidate: str, target_key: str) -> bool:
    candidate_key = normalize_for_compare(candidate)

    if not candidate_key or not target_key:
        return False

    return (
        candidate_key == target_key
        or candidate_key in target_key
        or target_key in candidate_key
    )


def cut_after_noise_sections(text: str) -> str:
    if not text:
        return ""

    upper_text = text.upper()
    cut_positions = []

    for marker in NOISE_SECTION_MARKERS:
        index = upper_text.find(marker)
        if index != -1:
            cut_positions.append(index)

    if not cut_positions:
        return text

    cut_at = min(cut_positions)

    if cut_at < 120:
        return text

    return text[:cut_at].strip()


def cut_after_other_addresses(text: str, target_address: str = "") -> str:
    """
    If the page text contains the target restaurant address and later another
    Paris-style address, cut before the other address.

    This reduces contamination from sister restaurants on group websites.
    """
    if not text or not target_address:
        return text or ""

    target_key = extract_address_key(target_address)
    if not target_key:
        return text

    target_seen = False

    for match in ADDRESS_PATTERN.finditer(text):
        candidate_address = match.group(0)

        if same_address(candidate_address, target_key):
            target_seen = True
            continue

        if target_seen and match.start() > 120:
            return text[: match.start()].strip()

    return text


def fetch_website_text(
    url: str,
    target_name: str = "",
    target_address: str = "",
    max_chars: int = MAX_WEBSITE_CHARS,
) -> str:
    """
    Fetch visible text from the restaurant's official website.

    Conservative MVP behavior:
    - homepage only;
    - no crawling yet;
    - no JavaScript rendering;
    - short timeout;
    - remove common sister-restaurant sections;
    - cut after another address appears after the target address;
    - cap text before sending to the LLM.
    """
    if not is_probably_valid_url(url):
        return ""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; RestaurantKnowledgeBot/1.0; "
            "+private-personal-database)"
        )
    }

    try:
        response = requests.get(
            url.strip(),
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return ""

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    title = clean_text(soup.title.get_text(" ")) if soup.title else ""

    meta_description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        meta_description = clean_text(meta["content"])

    headings = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        value = clean_text(heading.get_text(" "))
        if value and value.upper() not in NOISE_SECTION_MARKERS:
            headings.append(value)

    body_text = clean_text(soup.get_text(" "))
    body_text = cut_after_noise_sections(body_text)
    body_text = cut_after_other_addresses(body_text, target_address=target_address)

    combined = "\n".join(
        part
        for part in [
            f"Title: {title}" if title else "",
            f"Meta description: {meta_description}" if meta_description else "",
            "Headings: " + " | ".join(headings[:20]) if headings else "",
            f"Target restaurant name: {target_name}" if target_name else "",
            f"Target address: {target_address}" if target_address else "",
            f"Body text: {body_text}" if body_text else "",
        ]
        if part
    )

    return combined[:max_chars]