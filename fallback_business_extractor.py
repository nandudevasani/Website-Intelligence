"""Fallback business/address extraction layer for scraper failures.

Uses usaddress for probabilistic address parsing when available,
with regex fallback. Schema.org JSON-LD is treated as source of truth.
"""

from bs4 import BeautifulSoup
import json
import re

try:
    import usaddress
except ImportError:  # pragma: no cover
    usaddress = None

OUTPUT_TEMPLATE = {
    "business_name": "",
    "street_address": "",
    "city": "",
    "state": "",
    "zip_code": "",
    "confidence_score": 0,
}

# Fallback regex patterns (used only when usaddress is unavailable)
CITY_STATE_ZIP_RE = re.compile(r"(?:,\s*|\b)([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,3}),?\s+([A-Za-z]{2})\s+(\d{5})")

_STREET_SUFFIXES = frozenset([
    "street", "st", "road", "rd", "avenue", "ave", "boulevard", "blvd",
    "lane", "ln", "drive", "dr", "court", "ct", "way", "place", "pl",
    "circle", "cir", "trail", "trl", "parkway", "pkwy", "highway", "hwy",
    "terrace", "ter", "pike",
])
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
STATE_RE = re.compile(r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b", re.IGNORECASE)
STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.#\-\s]+?\s(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Circle|Cir|Way|Parkway|Pkwy|Place|Pl|Trail|Trl|Highway|Hwy|Terrace|Ter|Pike)\b(?:[,.\s]+(?:Suite|Ste|Unit)\s*\w+)?",
    re.IGNORECASE,
)

_BOILERPLATE_EDGE_WORDS = re.compile(
    r"^(Home|Welcome|Welcome to|Index)\b\s*[-–—:|]?\s*|\s*[-–—:|]?\s*\b(Home|Welcome|Index)$",
    re.IGNORECASE,
)


def _clean_text(text):
    text = text or ""
    text = text.replace("\xa0", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" |-,:;")


def _clean_business_name(name):
    """Strip boilerplate edge words from business name."""
    cleaned = _clean_text(name)
    cleaned = _BOILERPLATE_EDGE_WORDS.sub("", cleaned).strip()
    cleaned = re.sub(r"^\s*[-–—:|]+\s*|\s*[-–—:|]+\s*$", "", cleaned).strip()
    return cleaned


def _parse_address_usaddress(text):
    """Parse address using usaddress library. Returns (street, city, state, zip) or None."""
    if usaddress is None:
        return None

    cleaned = _clean_text(text)
    if not cleaned:
        return None

    try:
        tagged, _ = usaddress.tag(cleaned)
    except usaddress.RepeatedLabelError:
        try:
            parsed = usaddress.parse(cleaned)
            tagged = {}
            for value, label in parsed:
                if label not in tagged:
                    tagged[label] = value
                else:
                    tagged[label] += " " + value
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
        "USPSBoxType", "USPSBoxID",
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


def _extract_city_state_zip(text):
    # Try usaddress first
    result = _parse_address_usaddress(text)
    if result:
        return result[1], result[2], result[3]

    # Fallback to regex
    match = CITY_STATE_ZIP_RE.search(_clean_text(text))
    if not match:
        return "", "", ""
    city = _clean_text(match.group(1))
    # Strip leading street suffix words that bled into the city
    city_words = city.split()
    while city_words and city_words[0].lower() in _STREET_SUFFIXES:
        city_words.pop(0)
    city = " ".join(city_words)
    return city, match.group(2).upper(), match.group(3)


def _extract_street(text):
    # Try usaddress first
    result = _parse_address_usaddress(text)
    if result and result[0]:
        return result[0]

    # Fallback to regex
    match = STREET_RE.search(_clean_text(text))
    return _clean_text(match.group(0)) if match else ""


def _extract_full_address(text):
    """Extract all address components at once using usaddress, falling back to regex."""
    result = _parse_address_usaddress(text)
    if result:
        return result  # (street, city, state, zip)

    street = ""
    match = STREET_RE.search(_clean_text(text))
    if match:
        street = _clean_text(match.group(0))
    city, state, zip_code = "", "", ""
    csz_match = CITY_STATE_ZIP_RE.search(_clean_text(text))
    if csz_match:
        city = _clean_text(csz_match.group(1))
        state = csz_match.group(2).upper()
        zip_code = csz_match.group(3)
    return street, city, state, zip_code


def _result(confidence, business_name="", street_address="", city="", state="", zip_code=""):
    out = dict(OUTPUT_TEMPLATE)
    out.update(
        {
            "business_name": _clean_text(business_name),
            "street_address": _clean_text(street_address),
            "city": _clean_text(city),
            "state": _clean_text(state).upper(),
            "zip_code": _clean_text(zip_code),
            "confidence_score": confidence,
        }
    )
    return out


def _method_og_site_name(soup):
    """Extract business name from og:site_name meta tag — confidence 88."""
    tag = soup.find("meta", attrs={"property": "og:site_name"})
    if tag:
        name = _clean_business_name(tag.get("content", ""))
        if name and len(name) > 1:
            return _result(88, business_name=name)
    return _result(0)


def _method_itemprop_name(soup):
    """Extract business name from itemprop='name' — confidence 85."""
    tag = soup.find(attrs={"itemprop": "name"})
    if tag:
        name = _clean_business_name(tag.get_text(" ", strip=True))
        if name and len(name) > 1:
            return _result(85, business_name=name)
    return _result(0)


def _method_meta_names(soup):
    """Extract business name from application-name or og:title — confidence 80."""
    for attr, val, conf in [
        ("name", "application-name", 82),
        ("name", "apple-mobile-web-app-title", 80),
        ("property", "og:title", 78),
    ]:
        tag = soup.find("meta", attrs={attr: val})
        if tag:
            name = _clean_business_name(tag.get("content", ""))
            if name and len(name) > 1:
                return _result(conf, business_name=name)
    return _result(0)


def _method_title_name(soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if not title:
        return _result(0)
    title = re.sub(r"\b(Home|Welcome|Official Website)\b", "", title, flags=re.IGNORECASE)
    title = re.split(r"\s*[|\-–:]\s*", title)[0]
    title = _clean_business_name(title)
    return _result(70, business_name=title) if title else _result(0)


def _method_footer_scan(soup):
    footer = soup.find("footer")
    if not footer:
        return _result(0)
    text = _clean_text(footer.get_text(" ", strip=True))
    street, city, state, zip_code = _extract_full_address(text)
    if street or zip_code:
        return _result(85, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def _method_contact_section(soup):
    keywords = re.compile(r"contact|location|address|office", re.IGNORECASE)
    chunks = []
    for node in soup.find_all(["section", "div", "article", "address", "p"]):
        marker = " ".join(
            [
                node.get("id", ""),
                " ".join(node.get("class", [])) if node.get("class") else "",
                node.get_text(" ", strip=True)[:120],
            ]
        )
        if keywords.search(marker):
            chunks.append(_clean_text(node.get_text(" ", strip=True)))
    if not chunks:
        return _result(0)
    joined = " | ".join(chunks)
    street, city, state, zip_code = _extract_full_address(joined)
    if street or zip_code:
        return _result(70, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def _method_regex_page_text(visible_text):
    street, city, state, zip_code = _extract_full_address(visible_text)
    if zip_code:
        return _result(70, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def _iter_jsonld_nodes(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            for child in _iter_jsonld_nodes(value):
                yield child
    elif isinstance(node, list):
        for item in node:
            for child in _iter_jsonld_nodes(item):
                yield child


def _method_schema_localbusiness(soup):
    """Extract from Schema.org JSON-LD — highest confidence source of truth."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in _iter_jsonld_nodes(data):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            type_text = " ".join(node_type) if isinstance(node_type, list) else str(node_type)
            if "localbusiness" not in type_text.lower():
                continue
            # Prefer legalName over name
            name = node.get("legalName", "") or node.get("name", "")
            name = _clean_business_name(name)
            addr = node.get("address", {})
            if isinstance(addr, dict):
                street = addr.get("streetAddress", "")
                city = addr.get("addressLocality", "")
                state = addr.get("addressRegion", "")
                zip_code = addr.get("postalCode", "")
            else:
                addr_text = _clean_text(str(addr))
                street, city, state, zip_code = _extract_full_address(addr_text)
            return _result(95, business_name=name, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def _decode_urlish(text):
    text = text.replace("+", " ")
    def repl(match):
        try:
            return bytes.fromhex(match.group(1)).decode("utf-8")
        except Exception:
            return match.group(0)
    return re.sub(r"%([0-9A-Fa-f]{2})", repl, text)


def _method_google_maps_embed(soup):
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "maps.google.com" not in src and "google.com/maps" not in src:
            continue
        params = re.findall(r"(?:[?&](q|query|destination|daddr)=)([^&]+)", src)
        blob = " ".join(_decode_urlish(v) for _, v in params) or _decode_urlish(src)
        street, city, state, zip_code = _extract_full_address(blob)
        if street or zip_code:
            return _result(65, street_address=street, city=city, state=state, zip_code=zip_code)
    return _result(0)


def _method_text_block_scoring(visible_text):
    blocks = [b.strip() for b in re.split(r"[\r\n]+", visible_text) if _clean_text(b)]
    best = (0, "")
    for block in blocks:
        score = 0
        if STREET_RE.search(block):
            score += 3
        if STATE_RE.search(block):
            score += 2
        if ZIP_RE.search(block):
            score += 2
        if "," in block:
            score += 1
        if score > best[0]:
            best = (score, block)
    if best[0] == 0:
        return _result(0)
    street, city, state, zip_code = _extract_full_address(best[1])
    confidence = 70 if zip_code else 50
    return _result(confidence, street_address=street, city=city, state=state, zip_code=zip_code)


def extract_fallback_business_data(html_content, page_url=""):
    """Run fallback extraction strategies and return best JSON-compatible dict.

    Schema.org JSON-LD is tried first and treated as source of truth.
    """
    soup = BeautifulSoup(html_content or "", "html.parser")
    visible_text = _clean_text(soup.get_text("\n", strip=True))

    # Schema first — if it has high confidence, use it directly
    schema_result = _method_schema_localbusiness(soup)
    if schema_result.get("confidence_score", 0) >= 95:
        # Fill in business name from title if schema didn't have one
        if not schema_result.get("business_name"):
            title_result = _method_title_name(soup)
            if title_result.get("business_name"):
                schema_result["business_name"] = title_result["business_name"]
        return schema_result

    candidates = [
        schema_result,
        _method_og_site_name(soup),
        _method_itemprop_name(soup),
        _method_meta_names(soup),
        _method_footer_scan(soup),
        _method_regex_page_text(visible_text),
        _method_google_maps_embed(soup),
        _method_title_name(soup),
        _method_contact_section(soup),
        _method_text_block_scoring(visible_text),
    ]

    best = max(candidates, key=lambda c: c.get("confidence_score", 0))

    if not best.get("business_name"):
        title_result = _method_title_name(soup)
        if title_result.get("business_name"):
            best["business_name"] = title_result["business_name"]

    if not (best.get("city") and best.get("state") and best.get("zip_code")):
        city, state, zip_code = _extract_city_state_zip(visible_text)
        best["city"] = best.get("city") or city
        best["state"] = best.get("state") or state
        best["zip_code"] = best.get("zip_code") or zip_code

    if not best.get("street_address"):
        best["street_address"] = _extract_street(visible_text)

    return {
        "business_name": _clean_business_name(best.get("business_name", "")),
        "street_address": _clean_text(best.get("street_address", "")),
        "city": _clean_text(best.get("city", "")),
        "state": _clean_text(best.get("state", "")).upper(),
        "zip_code": _clean_text(best.get("zip_code", "")),
        "confidence_score": int(best.get("confidence_score", 0) or 0),
    }
