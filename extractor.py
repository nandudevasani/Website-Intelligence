"""Single-file business extraction engine.

Merged from: business_extraction_helper.py + fallback_business_extractor.py +
python_business_extractor_bridge.py

Accepts JSON via stdin: { "html": "...", "page_url": "..." }
Returns JSON via stdout with extracted business data.
"""

from __future__ import annotations

import json
import re
import sys
from html import unescape
from typing import Any, Dict, Iterable, List

try:
    import usaddress
except ImportError:
    usaddress = None

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# SHARED CONSTANTS & HELPERS
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_TEMPLATE: Dict[str, Any] = {
    "business_name": "",
    "street_address": "",
    "city": "",
    "state": "",
    "zip_code": "",
    "confidence_score": 0,
}

_BUSINESS_TYPE_HINTS = (
    "localbusiness", "organization", "corporation", "professionalservice",
    "attorney", "dentist", "medicalbusiness", "store", "restaurant",
    "financialservice", "realestateagent",
)

_NAME_BLOCKLIST_RE = re.compile(
    r"^(home|welcome|contact|about|services?|privacy policy|terms|untitled|default|my site|my wordpress blog)$",
    re.IGNORECASE,
)

_BOILERPLATE_EDGE_WORDS = re.compile(
    r"^(Home|Welcome|Welcome to|Index)\b\s*[-–—:|]?\s*|\s*[-–—:|]?\s*\b(Home|Welcome|Index)$",
    re.IGNORECASE,
)

_BOILERPLATE_NAMES = re.compile(
    r"^(just a moment|checking your browser|attention required|verify you are human|"
    r"access denied|forbidden|not found|error|page not found|default page|"
    r"404|403|500|502|503|home|index|untitled|default|new|new page)$",
    re.IGNORECASE,
)

_PRIMARY_ADDRESS_HINT_RE = re.compile(
    r"\b(?:head\s*office|headquarters|hq|main\s*office|corporate\s*office|primary\s*office)\b",
    re.IGNORECASE,
)

_STREET_SUFFIXES = frozenset([
    "street", "st", "road", "rd", "avenue", "ave", "boulevard", "blvd",
    "lane", "ln", "drive", "dr", "court", "ct", "way", "place", "pl",
    "circle", "cir", "trail", "trl", "parkway", "pkwy", "highway", "hwy",
    "terrace", "ter", "pike",
])

_STREET_PATTERN = re.compile(
    r"\b(\d{1,5}\s[A-Za-z0-9.\s]+(?:Street|St|Road|Rd|Ave|Avenue|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Circle|Cir|Trail|Trl|Parkway|Pkwy|Highway|Hwy|Terrace|Ter|Pike))\b",
    re.IGNORECASE,
)
STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.#\-\s]+?\s(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Circle|Cir|Way|Parkway|Pkwy|Place|Pl|Trail|Trl|Highway|Hwy|Terrace|Ter|Pike)\b(?:[,.\s]+(?:Suite|Ste|Unit)\s*\w+)?",
    re.IGNORECASE,
)
_CITY_STATE_ZIP_PATTERN = re.compile(
    r"(?:,\s*|\b)([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,3}),?\s+([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?"
)
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\xa0", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |\n\t\r-,:;")


def _clean_business_name(name: str) -> str:
    cleaned = _clean_text(name)
    cleaned = _BOILERPLATE_EDGE_WORDS.sub("", cleaned).strip()
    cleaned = re.sub(r"^\s*[-–—:|]+\s*|\s*[-–—:|]+\s*$", "", cleaned).strip()
    if _BOILERPLATE_NAMES.match(cleaned):
        return ""
    return cleaned


def _html_to_text(html: str) -> str:
    html = html or ""
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?(p|div|li|section|article|address|footer|header|h\d)\b[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return _clean_text(html)


def _is_valid_business_name(name: str) -> bool:
    cleaned = _clean_text(name)
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 90:
        return False
    if _NAME_BLOCKLIST_RE.search(cleaned):
        return False
    if re.search(r"\.(?:com|net|org|info|biz|edu)$", cleaned, flags=re.IGNORECASE):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ADDRESS PARSING (usaddress preferred, regex fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_address_usaddress(text: str):
    if usaddress is None:
        return None
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    try:
        tagged, _ = usaddress.tag(cleaned)
    except Exception:
        try:
            parsed = usaddress.parse(cleaned)
            tagged = {}
            for value, label in parsed:
                tagged.setdefault(label, value)
        except Exception:
            return None

    street_parts = []
    for label in [
        "AddressNumber", "AddressNumberPrefix", "AddressNumberSuffix",
        "StreetNamePreDirectional", "StreetNamePreModifier", "StreetNamePreType",
        "StreetName", "StreetNamePostType", "StreetNamePostDirectional",
        "SubaddressType", "SubaddressIdentifier",
        "OccupancyType", "OccupancyIdentifier", "BuildingName",
        "USPSBoxType", "USPSBoxID", "USPSBoxGroupType", "USPSBoxGroupID",
    ]:
        val = tagged.get(label, "").strip(" ,")
        if val:
            street_parts.append(val)

    street = " ".join(street_parts)
    city = tagged.get("PlaceName", "").strip(" ,")
    state = tagged.get("StateName", "").strip(" ,").upper()
    zip_code = tagged.get("ZipCode", "").strip(" ,")
    if street or zip_code:
        return street, city, state, zip_code
    return None


def _extract_city_state_zip_regex(text: str):
    match = _CITY_STATE_ZIP_PATTERN.search(_clean_text(text))
    if not match:
        return "", "", ""
    city = _clean_text(match.group(1))
    city_words = city.split()
    while city_words and city_words[0].lower() in _STREET_SUFFIXES:
        city_words.pop(0)
    return " ".join(city_words), match.group(2).upper(), match.group(3)


def _extract_full_address(text: str):
    """Returns (street, city, state, zip) tuple."""
    result = _parse_address_usaddress(text)
    if result:
        return result
    street = ""
    m = STREET_RE.search(_clean_text(text))
    if m:
        street = _clean_text(m.group(0))
    city, state, zip_code = _extract_city_state_zip_regex(text)
    return street, city, state, zip_code


def extract_address_components(text: str) -> Dict[str, Any]:
    result = _parse_address_usaddress(_clean_text(text))
    if result:
        street, city, state, zip_code = result
        return {
            "street_address": street, "city": city, "state": state, "zip_code": zip_code,
            "confidence_score": 80 if zip_code else 70,
        }
    street, city, state, zip_code = _extract_city_state_zip_regex(text), "", "", ""
    m = _STREET_PATTERN.search(_clean_text(text))
    street_addr = _clean_text(m.group(1)) if m else ""
    city, state, zip_code = _extract_city_state_zip_regex(text)
    return {
        "street_address": street_addr, "city": city, "state": state, "zip_code": zip_code,
        "confidence_score": 70 if (street_addr or zip_code) else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# JSON-LD / SCHEMA.ORG HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_json_ld_blocks(html: str) -> Iterable[str]:
    pattern = re.compile(
        r"<script[^>]*type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html or ""):
        block = match.group(1).strip()
        if block:
            yield block


def _iter_schema_nodes(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_schema_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_schema_nodes(item)


def _is_local_business(node: Dict[str, Any]) -> bool:
    schema_type = node.get("@type", "")
    joined = " ".join(str(v).lower() for v in schema_type) if isinstance(schema_type, list) else str(schema_type).lower()
    return any(hint in joined for hint in _BUSINESS_TYPE_HINTS)


def parse_schema_business_data(html: str) -> Dict[str, Any]:
    """Parse Schema.org LocalBusiness JSON-LD — source of truth (confidence 95)."""
    best = dict(OUTPUT_TEMPLATE)
    for block in _extract_json_ld_blocks(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _iter_schema_nodes(data):
            if not _is_local_business(node):
                continue
            name = _clean_text(str(node.get("legalName", "") or node.get("name", "")))
            address = node.get("address", {})
            street = city = state = zip_code = ""
            if isinstance(address, dict):
                street = _clean_text(str(address.get("streetAddress", "")))
                city = _clean_text(str(address.get("addressLocality", "")))
                state = _clean_text(str(address.get("addressRegion", ""))).upper()
                zip_code = _clean_text(str(address.get("postalCode", "")))[:10]
            elif isinstance(address, str):
                parsed = extract_address_components(address)
                street = parsed["street_address"]
                city = parsed["city"]
                state = parsed["state"]
                zip_code = parsed["zip_code"]

            candidate = {
                "business_name": _clean_business_name(name) if name else "",
                "street_address": street, "city": city, "state": state, "zip_code": zip_code,
                "confidence_score": 95,
            }
            if not _is_valid_business_name(candidate["business_name"]):
                candidate["business_name"] = ""

            completeness = sum(bool(candidate[k]) for k in ("business_name", "street_address", "city", "state", "zip_code"))
            best_completeness = sum(bool(best[k]) for k in ("business_name", "street_address", "city", "state", "zip_code"))
            if completeness > best_completeness:
                best = candidate
    return best


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY EXTRACTION (regex-based, no BS4 required)
# ─────────────────────────────────────────────────────────────────────────────

def extract_business_name(html: str) -> Dict[str, Any]:
    html = html or ""
    schema_data = parse_schema_business_data(html)
    if schema_data.get("business_name") and _is_valid_business_name(schema_data["business_name"]):
        return {"business_name": schema_data["business_name"], "confidence_score": 95, "source": "schema_localbusiness"}

    for pattern, source, score in (
        (r"<meta[^>]+property=['\"]og:site_name['\"][^>]+content=['\"](.*?)['\"]", "og:site_name", 88),
        (r"<meta[^>]+content=['\"](.*?)['\"][^>]+property=['\"]og:site_name['\"]", "og:site_name", 88),
    ):
        m = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            candidate = _clean_business_name(m.group(1))
            if _is_valid_business_name(candidate):
                return {"business_name": candidate, "confidence_score": score, "source": source}

    m = re.search(r"<[^>]*itemprop=['\"]name['\"][^>]*>([^<]{2,80})", html, flags=re.IGNORECASE)
    if m:
        candidate = _clean_business_name(m.group(1))
        if _is_valid_business_name(candidate):
            return {"business_name": candidate, "confidence_score": 85, "source": "itemprop_name"}

    for meta_re, source, score in (
        (r"<meta[^>]+name=['\"]application-name['\"][^>]+content=['\"](.*?)['\"]", "application-name", 82),
        (r"<meta[^>]+name=['\"]apple-mobile-web-app-title['\"][^>]+content=['\"](.*?)['\"]", "apple-app-title", 80),
        (r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"](.*?)['\"]", "og:title", 78),
    ):
        m = re.search(meta_re, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            candidate = _clean_business_name(re.split(r"[|\-–:]", m.group(1))[0])
            if _is_valid_business_name(candidate):
                return {"business_name": candidate, "confidence_score": score, "source": source}

    footer_match = re.search(r"<footer[^>]*>(.*?)</footer>", html, flags=re.IGNORECASE | re.DOTALL)
    if footer_match:
        footer_text = _html_to_text(footer_match.group(1))
        copyright_match = re.search(r"(?:copyright|©)\s*\d{0,4}\s*([A-Za-z0-9&.,'\-\s]{2,80})", footer_text, flags=re.IGNORECASE)
        if copyright_match and _is_valid_business_name(copyright_match.group(1)):
            return {"business_name": _clean_business_name(copyright_match.group(1)), "confidence_score": 85, "source": "footer_copyright"}

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _clean_business_name(re.split(r"[|\-–:]", title_match.group(1))[0])
        if _is_valid_business_name(title):
            return {"business_name": title, "confidence_score": 70, "source": "title"}

    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if h1_match:
        h1_text = _clean_business_name(re.sub(r"<[^>]+>", " ", h1_match.group(1)))
        if _is_valid_business_name(h1_text):
            return {"business_name": h1_text, "confidence_score": 70, "source": "h1"}

    return {"business_name": "", "confidence_score": 0, "source": "none"}


def _address_from_html_fragments(html: str) -> Dict[str, Any]:
    fragments: List[str] = []
    for segment in re.split(r"</?(?:span|div|p|li|br|address|section|article|footer)[^>]*>", html or "", flags=re.IGNORECASE):
        cleaned = _clean_text(re.sub(r"<[^>]+>", " ", segment))
        if cleaned:
            fragments.append(cleaned)
    joined = " ".join(fragments)
    return extract_address_components(joined)


def extract_primary_address_components(text: str) -> Dict[str, Any]:
    flattened = _clean_text(text)
    if not flattened:
        return dict(OUTPUT_TEMPLATE)

    result = _parse_address_usaddress(flattened)
    if result and result[0] and result[1]:
        street, city, state, zip_code = result
        return {"street_address": street, "city": city, "state": state, "zip_code": zip_code, "confidence_score": 80 if zip_code else 70}

    street_matches = list(_STREET_PATTERN.finditer(flattened))
    if not street_matches:
        return extract_address_components(flattened)

    best: Dict[str, Any] = dict(OUTPUT_TEMPLATE)
    best_score = -1
    global_csz = _extract_city_state_zip_regex(flattened)

    for idx, match in enumerate(street_matches):
        street = _clean_text(match.group(1))
        start = match.start()
        end = street_matches[idx + 1].start() if idx + 1 < len(street_matches) else min(len(flattened), match.end() + 220)
        snippet = flattened[start:end]

        snippet_parsed = _parse_address_usaddress(snippet)
        if snippet_parsed and snippet_parsed[0]:
            sstreet, scity, sstate, szip = snippet_parsed
            csz = {"city": scity, "state": sstate, "zip_code": szip}
        else:
            c, s, z = _extract_city_state_zip_regex(snippet)
            csz = {"city": c, "state": s, "zip_code": z}

        if not csz["zip_code"] and global_csz[2]:
            csz = {"city": global_csz[0], "state": global_csz[1], "zip_code": global_csz[2]}
        elif not (csz["city"] and csz["state"]) and global_csz[0] and global_csz[1]:
            csz = {"city": csz["city"] or global_csz[0], "state": csz["state"] or global_csz[1], "zip_code": csz["zip_code"] or global_csz[2]}

        score = 1
        if csz["zip_code"]: score += 3
        if csz["city"] and csz["state"]: score += 2
        context = flattened[max(0, start - 120):end]
        if _PRIMARY_ADDRESS_HINT_RE.search(context): score += 4

        if score > best_score:
            best_score = score
            final_street = snippet_parsed[0] if snippet_parsed and snippet_parsed[0] else street
            best = {
                "street_address": final_street, "city": csz["city"], "state": csz["state"],
                "zip_code": csz["zip_code"], "confidence_score": 75 if csz["zip_code"] else 65,
            }
    return best


def enhanced_business_extraction(html: str) -> Dict[str, Any]:
    """Primary extraction — regex + JSON-LD, no BS4 required."""
    result = dict(OUTPUT_TEMPLATE)
    schema = parse_schema_business_data(html)
    name_data = extract_business_name(html)
    schema_has_address = bool(schema.get("street_address") or schema.get("zip_code"))

    if schema_has_address:
        result.update(schema)
    else:
        text = _html_to_text(html)
        parsed_data = extract_primary_address_components(text)
        fragment_data = _address_from_html_fragments(html)
        address_best = fragment_data if fragment_data.get("confidence_score", 0) > parsed_data.get("confidence_score", 0) else parsed_data
        for key in ("street_address", "city", "state", "zip_code"):
            result[key] = address_best.get(key, "")
        if result["street_address"] or result["zip_code"]:
            result["confidence_score"] = max(result["confidence_score"], 70)

    if schema.get("business_name") and schema.get("confidence_score", 0) >= 95:
        result["business_name"] = schema["business_name"]
        result["confidence_score"] = max(result["confidence_score"], 95)
    elif name_data.get("business_name"):
        result["business_name"] = name_data["business_name"]
        result["confidence_score"] = max(result["confidence_score"], int(name_data.get("confidence_score", 0)))

    footer_match = re.search(r"<footer[^>]*>(.*?)</footer>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if footer_match and not schema_has_address:
        footer_data = extract_address_components(_html_to_text(footer_match.group(1)))
        if footer_data.get("street_address") or footer_data.get("zip_code"):
            for key in ("street_address", "city", "state", "zip_code"):
                if not result[key]:
                    result[key] = footer_data.get(key, "")
            result["confidence_score"] = max(result["confidence_score"], 85)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK EXTRACTION (BeautifulSoup-based, used when primary confidence < 80)
# ─────────────────────────────────────────────────────────────────────────────

def _result(confidence, business_name="", street_address="", city="", state="", zip_code=""):
    out = dict(OUTPUT_TEMPLATE)
    out.update({
        "business_name": _clean_text(business_name),
        "street_address": _clean_text(street_address),
        "city": _clean_text(city),
        "state": _clean_text(state).upper(),
        "zip_code": _clean_text(zip_code),
        "confidence_score": confidence,
    })
    return out


def _bs4_schema_localbusiness(soup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in _iter_schema_nodes(data):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            type_text = " ".join(node_type) if isinstance(node_type, list) else str(node_type)
            if "localbusiness" not in type_text.lower():
                continue
            name = _clean_business_name(node.get("legalName", "") or node.get("name", ""))
            addr = node.get("address", {})
            if isinstance(addr, dict):
                street, city, state, zip_code = (
                    addr.get("streetAddress", ""), addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""), addr.get("postalCode", ""),
                )
            else:
                street, city, state, zip_code = _extract_full_address(_clean_text(str(addr)))
            return _result(95, business_name=name, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def extract_fallback_business_data(html_content: str, page_url: str = "") -> Dict[str, Any]:
    """BS4-based fallback extraction — used when primary confidence < 80."""
    if not _BS4_AVAILABLE:
        return dict(OUTPUT_TEMPLATE)

    soup = BeautifulSoup(html_content or "", "html.parser")
    visible_text = _clean_text(soup.get_text("\n", strip=True))

    schema_result = _bs4_schema_localbusiness(soup)
    if schema_result.get("confidence_score", 0) >= 95:
        if not schema_result.get("business_name"):
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            if title:
                title = re.split(r"\s*[|\-–:]\s*", title)[0]
                schema_result["business_name"] = _clean_business_name(title)
        return schema_result

    candidates = [schema_result]

    # og:site_name
    tag = soup.find("meta", attrs={"property": "og:site_name"})
    if tag:
        name = _clean_business_name(tag.get("content", ""))
        if name and len(name) > 1:
            candidates.append(_result(88, business_name=name))

    # itemprop name
    tag = soup.find(attrs={"itemprop": "name"})
    if tag:
        name = _clean_business_name(tag.get_text(" ", strip=True))
        if name and len(name) > 1:
            candidates.append(_result(85, business_name=name))

    # meta tags
    for attr, val, conf in [("name", "application-name", 82), ("name", "apple-mobile-web-app-title", 80), ("property", "og:title", 78)]:
        tag = soup.find("meta", attrs={attr: val})
        if tag:
            name = _clean_business_name(tag.get("content", ""))
            if name and len(name) > 1:
                candidates.append(_result(conf, business_name=name))
                break

    # footer scan
    footer = soup.find("footer")
    if footer:
        text = _clean_text(footer.get_text(" ", strip=True))
        street, city, state, zip_code = _extract_full_address(text)
        if street or zip_code:
            candidates.append(_result(85, street_address=street, city=city, state=state, zip_code=zip_code))

    # contact sections
    keywords = re.compile(r"contact|location|address|office", re.IGNORECASE)
    chunks = []
    for node in soup.find_all(["section", "div", "article", "address", "p"]):
        marker = " ".join([node.get("id", ""), " ".join(node.get("class", [])) if node.get("class") else "", node.get_text(" ", strip=True)[:120]])
        if keywords.search(marker):
            chunks.append(_clean_text(node.get_text(" ", strip=True)))
    if chunks:
        street, city, state, zip_code = _extract_full_address(" | ".join(chunks))
        if street or zip_code:
            candidates.append(_result(70, street_address=street, city=city, state=state, zip_code=zip_code))

    # Google Maps embed
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "maps.google.com" in src or "google.com/maps" in src:
            params = re.findall(r"(?:[?&](q|query|destination|daddr)=)([^&]+)", src)
            blob = " ".join(v.replace("+", " ") for _, v in params) or src
            street, city, state, zip_code = _extract_full_address(blob)
            if street or zip_code:
                candidates.append(_result(65, street_address=street, city=city, state=state, zip_code=zip_code))

    # page text
    street, city, state, zip_code = _extract_full_address(visible_text)
    if zip_code:
        candidates.append(_result(70, street_address=street, city=city, state=state, zip_code=zip_code))

    best = max(candidates, key=lambda c: c.get("confidence_score", 0))

    # fill gaps
    if not best.get("business_name"):
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if title:
            title = re.sub(r"\b(Home|Welcome|Official Website)\b", "", title, flags=re.IGNORECASE)
            title = re.split(r"\s*[|\-–:]\s*", title)[0]
            best["business_name"] = _clean_business_name(title)

    if not (best.get("city") and best.get("state") and best.get("zip_code")):
        city, state, zip_code = _extract_city_state_zip_regex(visible_text)
        best["city"] = best.get("city") or city
        best["state"] = best.get("state") or state
        best["zip_code"] = best.get("zip_code") or zip_code

    if not best.get("street_address"):
        m = STREET_RE.search(_clean_text(visible_text))
        if m:
            best["street_address"] = _clean_text(m.group(0))

    return {
        "business_name": _clean_business_name(best.get("business_name", "")),
        "street_address": _clean_text(best.get("street_address", "")),
        "city": _clean_text(best.get("city", "")),
        "state": _clean_text(best.get("state", "")).upper(),
        "zip_code": _clean_text(best.get("zip_code", "")),
        "confidence_score": int(best.get("confidence_score", 0) or 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MERGE & MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _merge_best(primary: dict, fallback: dict):
    out = dict(primary or {})
    fallback = fallback or {}
    name_source = "primary" if out.get("business_name") else "none"

    for key in ("business_name", "street_address", "city", "state", "zip_code"):
        if not out.get(key) and fallback.get(key):
            out[key] = fallback[key]
            if key == "business_name":
                name_source = "fallback"

    out["confidence_score"] = max(
        int(out.get("confidence_score", 0) or 0),
        int(fallback.get("confidence_score", 0) or 0),
    )
    return out, name_source


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        html = payload.get("html", "")
        page_url = payload.get("page_url", "")

        primary = enhanced_business_extraction(html)

        use_fallback = (
            _BS4_AVAILABLE
            and (
                int(primary.get("confidence_score", 0) or 0) < 80
                or not primary.get("business_name")
                or not primary.get("street_address")
            )
        )

        fallback = extract_fallback_business_data(html, page_url) if use_fallback else {}
        best, best_name_source = _merge_best(primary, fallback)

        sys.stdout.write(json.dumps({
            "ok": True,
            "primary": primary,
            "fallback": fallback,
            "best": best,
            "used_fallback": bool(fallback),
            "best_name_source": best_name_source,
        }))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
