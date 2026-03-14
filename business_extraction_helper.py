"""Independent helpers for extracting business identity and US address details.

This module uses the usaddress library for probabilistic address parsing
instead of brittle regex patterns. Schema.org JSON-LD data is treated as the
authoritative source of truth and is never overwritten by lower-confidence
fallback methods.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Dict, Iterable, List, Tuple

try:
    import usaddress
except ImportError:  # pragma: no cover - optional on systems without usaddress
    usaddress = None

OUTPUT_TEMPLATE: Dict[str, Any] = {
    "business_name": "",
    "street_address": "",
    "city": "",
    "state": "",
    "zip_code": "",
    "confidence_score": 0,
}

_PRIMARY_ADDRESS_HINT_RE = re.compile(
    r"\b(?:head\s*office|headquarters|hq|main\s*office|corporate\s*office|primary\s*office)\b",
    re.IGNORECASE,
)

_BUSINESS_TYPE_HINTS = (
    "localbusiness",
    "organization",
    "corporation",
    "professionalservice",
    "attorney",
    "dentist",
    "medicalbusiness",
    "store",
    "restaurant",
    "financialservice",
    "realestateagent",
)

_NAME_BLOCKLIST_RE = re.compile(
    r"^(home|welcome|contact|about|services?|privacy policy|terms|untitled|default|my site|my wordpress blog)$",
    re.IGNORECASE,
)

_BOILERPLATE_EDGE_WORDS = re.compile(
    r"^(Home|Welcome|Welcome to|Index)\b\s*[-–—:|]?\s*|\s*[-–—:|]?\s*\b(Home|Welcome|Index)$",
    re.IGNORECASE,
)

# Fallback regex patterns used only when usaddress is unavailable
_STREET_PATTERN = re.compile(
    r"\b(\d{1,5}\s[A-Za-z0-9.\s]+(?:Street|St|Road|Rd|Ave|Avenue|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Circle|Cir|Trail|Trl|Parkway|Pkwy|Highway|Hwy|Terrace|Ter|Pike))\b",
    re.IGNORECASE,
)
_CITY_STATE_ZIP_PATTERN = re.compile(
    r"(?:,\s*|\b)([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,3}),?\s+([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?"
)

_STREET_SUFFIXES = frozenset([
    "street", "st", "road", "rd", "avenue", "ave", "boulevard", "blvd",
    "lane", "ln", "drive", "dr", "court", "ct", "way", "place", "pl",
    "circle", "cir", "trail", "trl", "parkway", "pkwy", "highway", "hwy",
    "terrace", "ter", "pike",
])


def _clean_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |\n\t\r-")


def _clean_business_name(name: str) -> str:
    """Strip boilerplate edge words like 'Home', 'Welcome', 'Index' from a business name."""
    cleaned = _clean_text(name)
    cleaned = _BOILERPLATE_EDGE_WORDS.sub("", cleaned).strip()
    # Strip trailing separators left after removal
    cleaned = re.sub(r"^\s*[-–—:|]+\s*|\s*[-–—:|]+\s*$", "", cleaned).strip()
    return cleaned


def _html_to_text(html: str) -> str:
    html = html or ""
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?(p|div|li|section|article|address|footer|header|h\d)\b[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return _clean_text(html)


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
    if isinstance(schema_type, list):
        joined = " ".join(str(value).lower() for value in schema_type)
    else:
        joined = str(schema_type).lower()
    return any(hint in joined for hint in _BUSINESS_TYPE_HINTS)


def _is_valid_business_name(name: str) -> bool:
    cleaned = _clean_text(name)
    if not cleaned:
        return False
    if len(cleaned) < 3 or len(cleaned) > 90:
        return False
    if _NAME_BLOCKLIST_RE.search(cleaned):
        return False
    if re.search(r"\.(?:com|net|org|info|biz|edu)$", cleaned, flags=re.IGNORECASE):
        return False
    return True


# ---------------------------------------------------------------------------
# usaddress-based structured parser
# ---------------------------------------------------------------------------

_STREET_COMPONENTS = frozenset([
    "AddressNumber",
    "AddressNumberPrefix",
    "AddressNumberSuffix",
    "StreetName",
    "StreetNamePreDirectional",
    "StreetNamePreModifier",
    "StreetNamePreType",
    "StreetNamePostDirectional",
    "StreetNamePostType",
    "SubaddressIdentifier",
    "SubaddressType",
    "OccupancyType",
    "OccupancyIdentifier",
    "BuildingName",
    "CornerOf",
    "USPSBoxType",
    "USPSBoxID",
    "USPSBoxGroupType",
    "USPSBoxGroupID",
])


def parse_us_address_robust(raw_string: str) -> Dict[str, str] | None:
    """Parse a US address using usaddress (probabilistic parser).

    Returns a dict with street, city, state, zip or None on failure.
    Suite/unit designators stay with the street field.
    """
    if usaddress is None:
        return None

    cleaned = _clean_text(raw_string)
    if not cleaned:
        return None

    try:
        tagged_address, address_type = usaddress.tag(cleaned)
    except usaddress.RepeatedLabelError:
        # Multiple addresses in string — try to parse just the first
        try:
            parsed = usaddress.parse(cleaned)
            tagged_address = {}
            for value, label in parsed:
                if label not in tagged_address:
                    tagged_address[label] = value
                else:
                    tagged_address[label] += " " + value
        except Exception:
            return None
    except Exception:
        return None

    street_parts = []
    for label in [
        "AddressNumber", "AddressNumberPrefix", "AddressNumberSuffix",
        "StreetNamePreDirectional", "StreetNamePreModifier", "StreetNamePreType",
        "StreetName", "StreetNamePostType", "StreetNamePostDirectional",
        "SubaddressType", "SubaddressIdentifier",
        "OccupancyType", "OccupancyIdentifier",
        "BuildingName",
        "USPSBoxType", "USPSBoxID", "USPSBoxGroupType", "USPSBoxGroupID",
    ]:
        val = tagged_address.get(label, "").strip(" ,")
        if val:
            street_parts.append(val)

    return {
        "street": " ".join(street_parts),
        "city": tagged_address.get("PlaceName", "").strip(" ,"),
        "state": tagged_address.get("StateName", "").strip(" ,").upper(),
        "zip": tagged_address.get("ZipCode", "").strip(" ,"),
    }


# ---------------------------------------------------------------------------
# Fallback regex parsing (used when usaddress is not installed)
# ---------------------------------------------------------------------------

def _detect_city_state_zip_regex(text: str) -> Dict[str, str]:
    cleaned = _clean_text(text)
    match = _CITY_STATE_ZIP_PATTERN.search(cleaned)
    if not match:
        return {"city": "", "state": "", "zip_code": ""}
    city = _clean_text(match.group(1))
    state = match.group(2).upper()
    zip_code = match.group(3)
    # Strip leading street suffix words that bled into the city
    # e.g., "Lane Cape Coral" -> "Cape Coral"
    city_words = city.split()
    while city_words and city_words[0].lower() in _STREET_SUFFIXES:
        city_words.pop(0)
    city = " ".join(city_words)
    return {"city": city, "state": state, "zip_code": zip_code}


def _extract_address_regex(text: str) -> Dict[str, Any]:
    flattened = _clean_text(text)
    street_address = ""
    street_match = _STREET_PATTERN.search(flattened)
    if street_match:
        street_address = _clean_text(street_match.group(1))
    csz = _detect_city_state_zip_regex(flattened)
    return {
        "street_address": street_address,
        "city": csz["city"],
        "state": csz["state"],
        "zip_code": csz["zip_code"],
        "confidence_score": 70 if street_address or csz["zip_code"] else 0,
    }


# ---------------------------------------------------------------------------
# Public address extraction (prefers usaddress, falls back to regex)
# ---------------------------------------------------------------------------

def extract_address_components(text: str) -> Dict[str, Any]:
    """Extract US street/city/state/zip from free text.

    Uses usaddress when available for accurate component separation,
    falls back to regex patterns otherwise.
    """
    flattened = _clean_text(text)
    if not flattened:
        return dict(OUTPUT_TEMPLATE)

    # Try usaddress first
    parsed = parse_us_address_robust(flattened)
    if parsed and (parsed["street"] or parsed["zip"]):
        return {
            "street_address": parsed["street"],
            "city": parsed["city"],
            "state": parsed["state"],
            "zip_code": parsed["zip"],
            "confidence_score": 80 if parsed["zip"] else 70,
        }

    # Fallback to regex
    return _extract_address_regex(flattened)


def detect_city_state_zip(text: str) -> Dict[str, str]:
    """Detect city/state/zip — uses usaddress when available."""
    parsed = parse_us_address_robust(text)
    if parsed and (parsed["city"] or parsed["zip"]):
        return {"city": parsed["city"], "state": parsed["state"], "zip_code": parsed["zip"]}
    return _detect_city_state_zip_regex(text)


def _address_from_html_fragments(html: str) -> Dict[str, Any]:
    """Reconstruct split DOM text fragments and run address extraction."""
    fragments: List[str] = []
    for segment in re.split(r"</?(?:span|div|p|li|br|address|section|article|footer)[^>]*>", html or "", flags=re.IGNORECASE):
        cleaned = _clean_text(re.sub(r"<[^>]+>", " ", segment))
        if cleaned:
            fragments.append(cleaned)

    joined = " ".join(fragments)
    return extract_address_components(joined)


def extract_primary_address_components(text: str) -> Dict[str, Any]:
    """Select a primary address when multiple candidate addresses are present."""
    flattened = _clean_text(text)
    if not flattened:
        return {
            "street_address": "",
            "city": "",
            "state": "",
            "zip_code": "",
            "confidence_score": 0,
        }

    # Try usaddress on the full text first
    parsed = parse_us_address_robust(flattened)
    if parsed and parsed["street"] and parsed["city"]:
        return {
            "street_address": parsed["street"],
            "city": parsed["city"],
            "state": parsed["state"],
            "zip_code": parsed["zip"],
            "confidence_score": 80 if parsed["zip"] else 70,
        }

    # Fall back to multi-candidate scoring with regex
    street_matches = list(_STREET_PATTERN.finditer(flattened))
    if not street_matches:
        return extract_address_components(flattened)

    best: Dict[str, Any] = {
        "street_address": "",
        "city": "",
        "state": "",
        "zip_code": "",
        "confidence_score": 0,
    }
    best_score = -1

    global_csz = detect_city_state_zip(flattened)

    for idx, match in enumerate(street_matches):
        street = _clean_text(match.group(1))
        start = match.start()
        end = street_matches[idx + 1].start() if idx + 1 < len(street_matches) else min(len(flattened), match.end() + 220)
        snippet = flattened[start:end]

        # Try usaddress on the snippet
        snippet_parsed = parse_us_address_robust(snippet)
        if snippet_parsed and snippet_parsed["street"]:
            csz = {"city": snippet_parsed["city"], "state": snippet_parsed["state"], "zip_code": snippet_parsed["zip"]}
        else:
            csz = _detect_city_state_zip_regex(snippet)

        if not csz["zip_code"] and global_csz["zip_code"]:
            csz = global_csz
        elif not (csz["city"] and csz["state"]) and global_csz["city"] and global_csz["state"]:
            csz = {
                "city": csz["city"] or global_csz["city"],
                "state": csz["state"] or global_csz["state"],
                "zip_code": csz["zip_code"] or global_csz["zip_code"],
            }
        score = 1
        if csz["zip_code"]:
            score += 3
        if csz["city"] and csz["state"]:
            score += 2

        context_start = max(0, start - 120)
        context = flattened[context_start:end]
        if _PRIMARY_ADDRESS_HINT_RE.search(context):
            score += 4

        if score > best_score:
            best_score = score
            # Use usaddress street if available (preserves Suite info properly)
            final_street = snippet_parsed["street"] if snippet_parsed and snippet_parsed["street"] else street
            best = {
                "street_address": final_street,
                "city": csz["city"],
                "state": csz["state"],
                "zip_code": csz["zip_code"],
                "confidence_score": 75 if csz["zip_code"] else 65,
            }

    return best


# ---------------------------------------------------------------------------
# Schema.org JSON-LD parsing (source of truth — confidence 95)
# ---------------------------------------------------------------------------

def parse_schema_business_data(html: str) -> Dict[str, Any]:
    """Parse LocalBusiness JSON-LD blocks and return structured fields.

    Schema.org data is treated as the authoritative source of truth.
    """
    best = dict(OUTPUT_TEMPLATE)

    for block in _extract_json_ld_blocks(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        for node in _iter_schema_nodes(data):
            if not _is_local_business(node):
                continue

            # Prefer legalName over name for business name
            name = _clean_text(str(node.get("legalName", "") or node.get("name", "")))
            if not name:
                name = _clean_text(str(node.get("name", "")))

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
                "street_address": street,
                "city": city,
                "state": state,
                "zip_code": zip_code,
                "confidence_score": 95,
            }

            if not _is_valid_business_name(candidate["business_name"]):
                candidate["business_name"] = ""

            completeness = sum(bool(candidate[k]) for k in ("business_name", "street_address", "city", "state", "zip_code"))
            best_completeness = sum(bool(best[k]) for k in ("business_name", "street_address", "city", "state", "zip_code"))
            if completeness > best_completeness:
                best = candidate

    return best


# ---------------------------------------------------------------------------
# Business name extraction with source-priority
# ---------------------------------------------------------------------------

def extract_business_name(html: str) -> Dict[str, Any]:
    """Extract business name with source-priority and confidence scoring.

    Priority order:
      1. Schema.org LocalBusiness (95%)
      2. og:site_name (88%)
      3. itemprop="name" (85%)
      4. application-name meta (82%)
      5. apple-mobile-web-app-title (80%)
      6. og:title (78%)
      7. Footer copyright (85%)
      8. <title> tag (70%)
      9. <h1> tag (70%)
    """
    html = html or ""

    # Priority 1: Schema.org LocalBusiness name (source of truth)
    schema_data = parse_schema_business_data(html)
    if schema_data.get("business_name") and _is_valid_business_name(schema_data["business_name"]):
        return {
            "business_name": schema_data["business_name"],
            "confidence_score": 95,
            "source": "schema_localbusiness",
        }

    # Priority 2: og:site_name (usually the cleanest brand name)
    og_site_name_re = r"<meta[^>]+property=['\"]og:site_name['\"][^>]+content=['\"](.*?)['\"]"
    og_match = re.search(og_site_name_re, html, flags=re.IGNORECASE | re.DOTALL)
    if not og_match:
        # Try reversed attribute order
        og_site_name_re2 = r"<meta[^>]+content=['\"](.*?)['\"][^>]+property=['\"]og:site_name['\"]"
        og_match = re.search(og_site_name_re2, html, flags=re.IGNORECASE | re.DOTALL)
    if og_match:
        candidate = _clean_business_name(og_match.group(1))
        if _is_valid_business_name(candidate):
            return {
                "business_name": candidate,
                "confidence_score": 88,
                "source": "og:site_name",
            }

    # Priority 3: itemprop="name" (microdata — strong signal)
    itemprop_match = re.search(
        r"<[^>]*itemprop=['\"]name['\"][^>]*>([^<]{2,80})",
        html, flags=re.IGNORECASE,
    )
    if itemprop_match:
        candidate = _clean_business_name(itemprop_match.group(1))
        if _is_valid_business_name(candidate):
            return {
                "business_name": candidate,
                "confidence_score": 85,
                "source": "itemprop_name",
            }

    # Priority 4-6: Other meta tags
    for meta_re, source, score in (
        (r"<meta[^>]+name=['\"]application-name['\"][^>]+content=['\"](.*?)['\"]", "application-name", 82),
        (r"<meta[^>]+name=['\"]apple-mobile-web-app-title['\"][^>]+content=['\"](.*?)['\"]", "apple-app-title", 80),
        (r"<meta[^>]+property=['\"]og:title['\"][^>]+content=['\"](.*?)['\"]", "og:title", 78),
    ):
        m = re.search(meta_re, html, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        candidate = _clean_business_name(re.split(r"[|\-–:]", m.group(1))[0])
        if _is_valid_business_name(candidate):
            return {
                "business_name": candidate,
                "confidence_score": score,
                "source": source,
            }

    # Priority 7: Footer copyright
    footer_match = re.search(r"<footer[^>]*>(.*?)</footer>", html, flags=re.IGNORECASE | re.DOTALL)
    if footer_match:
        footer_text = _html_to_text(footer_match.group(1))
        copyright_match = re.search(
            r"(?:copyright|©)\s*\d{0,4}\s*([A-Za-z0-9&.,'\-\s]{2,80})",
            footer_text,
            flags=re.IGNORECASE,
        )
        if copyright_match and _is_valid_business_name(copyright_match.group(1)):
            return {
                "business_name": _clean_business_name(copyright_match.group(1)),
                "confidence_score": 85,
                "source": "footer_copyright",
            }

    # Priority 8: <title> tag
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _clean_business_name(re.split(r"[|\-–:]", title_match.group(1))[0])
        if _is_valid_business_name(title):
            return {
                "business_name": title,
                "confidence_score": 70,
                "source": "title",
            }

    # Priority 9: <h1> tag
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    if h1_match:
        h1_text = _clean_business_name(re.sub(r"<[^>]+>", " ", h1_match.group(1)))
        if _is_valid_business_name(h1_text):
            return {
                "business_name": h1_text,
                "confidence_score": 70,
                "source": "h1",
            }

    return {"business_name": "", "confidence_score": 0, "source": "none"}


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def enhanced_business_extraction(html: str) -> Dict[str, Any]:
    """Return best structured business details from raw HTML.

    Schema.org JSON-LD data is treated as source of truth — its fields
    are never overwritten by lower-confidence regex/fallback results.
    """
    result = dict(OUTPUT_TEMPLATE)

    schema = parse_schema_business_data(html)
    name_data = extract_business_name(html)

    # Schema.org data is authoritative — use it first and protect it
    schema_has_address = bool(schema.get("street_address") or schema.get("zip_code"))

    if schema_has_address:
        result.update(schema)
    else:
        text = _html_to_text(html)
        parsed_data = extract_primary_address_components(text)
        fragment_data = _address_from_html_fragments(html)

        address_best = parsed_data
        if fragment_data.get("confidence_score", 0) > parsed_data.get("confidence_score", 0):
            address_best = fragment_data

        for key in ("street_address", "city", "state", "zip_code"):
            result[key] = address_best.get(key, "")

        if result["street_address"] or result["zip_code"]:
            result["confidence_score"] = max(result["confidence_score"], 70)

    # Apply business name — schema name takes priority
    if schema.get("business_name") and schema.get("confidence_score", 0) >= 95:
        result["business_name"] = schema["business_name"]
        result["confidence_score"] = max(result["confidence_score"], 95)
    elif name_data.get("business_name"):
        result["business_name"] = name_data["business_name"]
        result["confidence_score"] = max(result["confidence_score"], int(name_data.get("confidence_score", 0)))

    # Footer address fallback — only fill in blanks, never overwrite schema data
    footer_match = re.search(r"<footer[^>]*>(.*?)</footer>", html or "", flags=re.IGNORECASE | re.DOTALL)
    if footer_match and not schema_has_address:
        footer_data = extract_address_components(_html_to_text(footer_match.group(1)))
        if footer_data.get("street_address") or footer_data.get("zip_code"):
            for key in ("street_address", "city", "state", "zip_code"):
                if not result[key]:
                    result[key] = footer_data.get(key, "")
            result["confidence_score"] = max(result["confidence_score"], 85)

    return result
