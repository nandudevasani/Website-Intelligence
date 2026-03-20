#!/usr/bin/env python3
"""
extractor.py — Website Intelligence v4.0
DOM-first business info extractor. Reads JSON from stdin, writes JSON to stdout.

Libraries (in priority order):
  extruct     → structured data (JSON-LD, microdata, OpenGraph, microformat, RDFa)
  phonenumbers → phone parsing & formatting
  usaddress   → US address tagging
  beautifulsoup4 + lxml → DOM parsing
"""

import json
import sys
import re
from urllib.parse import urlparse

# ── Optional library imports ─────────────────────────────────────────────────

try:
    import extruct
    HAS_EXTRUCT = True
except ImportError:
    HAS_EXTRUCT = False

try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False

try:
    import usaddress
    HAS_USADDRESS = True
except ImportError:
    HAS_USADDRESS = False

from bs4 import BeautifulSoup

# ── Social platform registry ─────────────────────────────────────────────────

SOCIAL_PLATFORMS = {
    'facebook.com':    ('Facebook',        'fb'),
    'instagram.com':   ('Instagram',       'instagram'),
    'twitter.com':     ('Twitter/X',       'tw'),
    'x.com':           ('Twitter/X',       'tw'),
    'linkedin.com':    ('LinkedIn',        'li'),
    'youtube.com':     ('YouTube',         'yt'),
    'tiktok.com':      ('TikTok',          'tt'),
    'pinterest.com':   ('Pinterest',       'pi'),
    'yelp.com':        ('Yelp',            'yl'),
    'bbb.org':         ('BBB',             'bbb'),
    'nextdoor.com':    ('Nextdoor',        'nd'),
    'github.com':      ('GitHub',          'gh'),
    'threads.net':     ('Threads',         'threads'),
    'snapchat.com':    ('Snapchat',        'snap'),
    'wa.me':           ('WhatsApp',        'wa'),
    'whatsapp.com':    ('WhatsApp',        'wa'),
    't.me':            ('Telegram',        'tg'),
    'g.page':          ('Google Business', 'gb'),
    'houzz.com':       ('Houzz',           'houzz'),
    'angi.com':        ('Angi',            'angi'),
    'thumbtack.com':   ('Thumbtack',       'tt2'),
}

# For google maps we need substring matching
GOOGLE_MAPS_RE = re.compile(r'google\.com/maps', re.I)

SKIP_PATHS = {
    'share', 'sharer', 'intent', 'login', 'signup', 'register',
    'help', 'about', 'terms', 'privacy', 'developer', 'ads',
    'business', 'legal', 'policies', 'widget', 'embed', 'badge',
}

BOILERPLATE_NAMES = {
    'home', 'untitled', 'coming soon', 'under construction', 'my website',
    'my site', 'just another wordpress site', 'my blog', 'error',
    'access denied', 'forbidden', 'not found', '404', 'maintenance mode',
    'website', 'new site', 'test', 'example', 'placeholder', 'page',
    'welcome', 'index', 'main',
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def classify_social(url):
    """Return {platform, url, icon} if URL is a recognised social profile, else None."""
    if not url or not url.startswith('http'):
        return None
    try:
        parsed = urlparse(url)
        host   = parsed.netloc.lower().lstrip('www.')
        path   = parsed.path.strip('/')
        path_parts = [p for p in path.split('/') if p]

        # Google Maps special case
        if GOOGLE_MAPS_RE.search(url):
            return {'platform': 'Google Business', 'url': url, 'icon': 'gb'}

        for domain, (name, icon) in SOCIAL_PLATFORMS.items():
            if host == domain or host.endswith('.' + domain):
                # Skip sharing / widget links
                if path_parts and path_parts[0] in SKIP_PATHS:
                    return None
                # Require a profile path (except WhatsApp / Telegram)
                if not path_parts and name not in ('WhatsApp', 'Telegram'):
                    return None
                return {'platform': name, 'url': url, 'icon': icon}
    except Exception:
        pass
    return None


def add_phone(raw, phones_list):
    if not raw:
        return
    digits = re.sub(r'\D', '', raw)
    if len(digits) < 7:
        return
    existing_digits = [re.sub(r'\D', '', p) for p in phones_list]
    if digits in existing_digits:
        return
    phones_list.append(raw.strip())


def add_email(email, emails_list):
    JUNK = re.compile(
        r'noreply|no-reply|donotreply|example\.com|test\.com|'
        r'sentry|wixpress|google\.com|schema\.org|wordpress|'
        r'w3\.org|jquery|gravatar|placeholder', re.I
    )
    if not email or '@' not in email or JUNK.search(email):
        return
    email = email.lower().strip()
    if email not in emails_list:
        emails_list.append(email)


def parse_address_usaddress(text):
    """Use usaddress to tag and return structured address fields."""
    if not HAS_USADDRESS or not text:
        return {}
    try:
        tagged, _ = usaddress.tag(text)
        street_keys = {
            'AddressNumber', 'StreetNamePreDirectional', 'StreetName',
            'StreetNamePostType', 'StreetNamePostDirectional',
            'OccupancyType', 'OccupancyIdentifier',
        }
        street_parts = []
        result = {}
        for key, val in tagged.items():
            if key in street_keys:
                street_parts.append(val)
            elif key == 'PlaceName':
                result['city'] = val
            elif key == 'StateName':
                result['state'] = val
            elif key == 'ZipCode':
                result['zip'] = val
        if street_parts:
            result['street'] = ' '.join(street_parts)
        return result
    except Exception:
        return {}


def format_phone(raw):
    """Format a raw phone string via phonenumbers, fall back to raw."""
    if HAS_PHONENUMBERS:
        try:
            pn = phonenumbers.parse(raw, 'US')
            if phonenumbers.is_valid_number(pn):
                return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL)
        except Exception:
            pass
    return raw.strip()


def is_boilerplate(name):
    if not name:
        return True
    return name.lower().strip() in BOILERPLATE_NAMES or len(name.strip()) < 2


# ── Main extraction ─────────────────────────────────────────────────────────

def extract(html, url, domain):
    soup     = BeautifulSoup(html, 'lxml')
    base_url = f'https://{domain}'

    result = {
        'business_name':   None,
        'phones':          [],
        'emails':          [],
        'address':         {'street': '', 'city': '', 'state': '', 'zip': ''},
        'social_signals':  [],
        'meta_description': None,
        'business_type':   None,
        'confidence_score': 0,
        'country':         {'code': 'US', 'name': 'United States'},
    }

    # ── 1. STRUCTURED DATA via extruct ──────────────────────────────────────
    if HAS_EXTRUCT and html:
        try:
            data = extruct.extract(
                html,
                base_url=base_url,
                syntaxes=['json-ld', 'microdata', 'opengraph', 'microformat'],
                uniform=True,
            )

            # JSON-LD — prefer LocalBusiness / Organization types
            for item in data.get('json-ld', []):
                typ      = item.get('@type', '')
                type_str = (typ[0] if isinstance(typ, list) else str(typ))
                if not re.search(r'business|organization|corporation|store|service|restaurant|hotel|church|clinic', type_str, re.I):
                    continue

                # Business name
                if not result['business_name']:
                    result['business_name'] = item.get('legalName') or item.get('name')
                result['business_type'] = type_str

                # Address
                addr = item.get('address', {})
                if isinstance(addr, dict) and not result['address']['street']:
                    result['address']['street'] = addr.get('streetAddress', '')
                    result['address']['city']   = addr.get('addressLocality', '')
                    result['address']['state']  = addr.get('addressRegion', '')
                    result['address']['zip']    = str(addr.get('postalCode', ''))
                elif isinstance(addr, str) and not result['address']['street']:
                    parsed = parse_address_usaddress(addr)
                    if parsed.get('street'):
                        result['address'].update(parsed)

                # Phone
                phone = item.get('telephone', '')
                if phone:
                    add_phone(format_phone(str(phone)), result['phones'])

                # Email
                email = item.get('email', '')
                if email:
                    add_email(str(email), result['emails'])

                # Social — sameAs
                same_as = item.get('sameAs', [])
                if isinstance(same_as, str):
                    same_as = [same_as]
                seen = {s['platform'] for s in result['social_signals']}
                for link in same_as:
                    sig = classify_social(link)
                    if sig and sig['platform'] not in seen:
                        result['social_signals'].append(sig)
                        seen.add(sig['platform'])

                break  # Use first matching schema item

            # OpenGraph fallback for name
            og_list = data.get('opengraph', [])
            og = og_list[0] if og_list else {}
            if not result['business_name']:
                result['business_name'] = og.get('og:site_name') or og.get('twitter:site')

        except Exception:
            pass

    # ── 2. DOM PARSING — BeautifulSoup selectors ───────────────────────────

    # Meta description
    meta_desc = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
    if meta_desc:
        result['meta_description'] = (meta_desc.get('content') or '').strip()

    # ── Business name fallback chain ──────────────────────────────────────

    if not result['business_name']:
        # og:site_name
        og_site = (soup.find('meta', property='og:site_name') or
                   soup.find('meta', attrs={'name': 'og:site_name'}))
        if og_site:
            result['business_name'] = (og_site.get('content') or '').strip() or None

    if not result['business_name']:
        # itemprop="name" (prefer elements in header context)
        for el in soup.select('header [itemprop="name"], [itemtype] [itemprop="name"], [itemprop="name"]'):
            text = el.get_text(strip=True)
            if text and not is_boilerplate(text) and len(text) < 100:
                result['business_name'] = text
                break

    if not result['business_name']:
        # Logo alt text — most logos carry the company name
        for img in soup.select('header img[alt], .logo img[alt], #logo img[alt], [class*="logo"] img[alt], [id*="logo"] img[alt]'):
            alt = (img.get('alt') or '').strip()
            if alt and not is_boilerplate(alt) and 2 < len(alt) < 80:
                result['business_name'] = alt
                break

    if not result['business_name']:
        # aria-label on header / nav
        for el in soup.select('header[aria-label], nav[aria-label], [role="banner"][aria-label]'):
            label = (el.get('aria-label') or '').strip()
            if label and not is_boilerplate(label) and len(label) < 80:
                result['business_name'] = label
                break

    if not result['business_name']:
        # Copyright notice in footer
        footer = soup.find('footer')
        if footer:
            ft = footer.get_text(' ', strip=True)
            m = re.search(
                r'[©Cc]opyright\s+(?:\d{4}[\-–]\d{4}|\d{4})?\s*'
                r'(.+?)(?=\s*\.|,\s|LLC|Inc\.|Corp\.|Ltd\.|All rights)',
                ft
            )
            if m:
                candidate = m.group(1).strip()
                if candidate and not is_boilerplate(candidate) and len(candidate) < 80:
                    result['business_name'] = candidate

    if not result['business_name']:
        # Title tag — first segment before separator
        title_tag = soup.find('title')
        if title_tag:
            t = title_tag.get_text(strip=True)
            for sep in [' | ', ' - ', ' – ', ' — ', ' :: ']:
                if sep in t:
                    t = t.split(sep)[0].strip()
                    break
            if t and not is_boilerplate(t) and 2 < len(t) < 100:
                result['business_name'] = t

    if not result['business_name']:
        # H1 as last resort
        h1 = soup.find('h1')
        if h1:
            text = h1.get_text(strip=True)
            if text and not is_boilerplate(text) and len(text) < 100:
                result['business_name'] = text

    # ── Phones — DOM sources first, then phonenumbers full-page scan ──────

    # tel: links (most reliable)
    for a in soup.find_all('a', href=re.compile(r'^tel:', re.I)):
        raw = a['href'][4:].strip()
        add_phone(format_phone(raw), result['phones'])

    # itemprop="telephone"
    for el in soup.find_all(attrs={'itemprop': 'telephone'}):
        raw = el.get_text(strip=True) or el.get('content', '')
        add_phone(format_phone(raw), result['phones'])

    # phonenumbers full-page scan (if still empty)
    if HAS_PHONENUMBERS and not result['phones']:
        body_text = soup.get_text(' ')
        for match in phonenumbers.PhoneNumberMatcher(body_text, 'US'):
            fmt = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.NATIONAL)
            add_phone(fmt, result['phones'])

    # ── Emails — mailto links first, then itemprop ────────────────────────

    for a in soup.find_all('a', href=re.compile(r'^mailto:', re.I)):
        email = a['href'][7:].split('?')[0].strip().lower()
        add_email(email, result['emails'])

    for el in soup.find_all(attrs={'itemprop': 'email'}):
        email = el.get_text(strip=True) or el.get('content', '')
        add_email(email.lower(), result['emails'])

    # ── Address — four DOM sources, each more permissive ─────────────────

    addr = result['address']

    # Source 1: microdata PostalAddress block
    if not addr['street']:
        postal = soup.find(attrs={'itemtype': re.compile(r'PostalAddress', re.I)})
        if postal:
            def gp(parent, prop):
                el = parent.find(attrs={'itemprop': prop})
                return el.get_text(strip=True) if el else ''
            addr['street'] = gp(postal, 'streetAddress')
            addr['city']   = gp(postal, 'addressLocality')
            addr['state']  = gp(postal, 'addressRegion')
            addr['zip']    = gp(postal, 'postalCode')

    # Source 2: scattered itemprop without PostalAddress wrapper
    if not addr['street']:
        street_el = soup.find(attrs={'itemprop': 'streetAddress'})
        city_el   = soup.find(attrs={'itemprop': 'addressLocality'})
        state_el  = soup.find(attrs={'itemprop': 'addressRegion'})
        zip_el    = soup.find(attrs={'itemprop': 'postalCode'})
        if street_el:
            addr['street'] = street_el.get_text(strip=True)
            if city_el:  addr['city']  = city_el.get_text(strip=True)
            if state_el: addr['state'] = state_el.get_text(strip=True)
            if zip_el:   addr['zip']   = zip_el.get_text(strip=True)

    # Source 3: hCard / vCard microformat
    if not addr['street']:
        adr_el = soup.find(class_='adr')
        if adr_el:
            for cls, field in [('street-address','street'),('locality','city'),('region','state'),('postal-code','zip')]:
                el = adr_el.find(class_=cls)
                if el:
                    addr[field] = el.get_text(strip=True)

    # Source 4: HTML5 <address> element → usaddress
    if not addr['street']:
        addr_el = soup.find('address')
        if addr_el:
            text   = addr_el.get_text(' ', strip=True)
            parsed = parse_address_usaddress(text)
            if parsed.get('street'):
                addr.update(parsed)

    # Source 5: Contact-like sections → usaddress scan
    if not addr['street'] and HAS_USADDRESS:
        for section in soup.select('footer, [class*="contact"], [id*="contact"], [class*="address"]'):
            text   = section.get_text(' ', strip=True)
            parsed = parse_address_usaddress(text)
            if parsed.get('street') and parsed.get('city'):
                addr.update(parsed)
                break

    # ── Social signals — DOM link scan ───────────────────────────────────

    seen_platforms = {s['platform'] for s in result['social_signals']}
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        if not href.startswith('http'):
            continue
        sig = classify_social(href)
        if sig and sig['platform'] not in seen_platforms:
            result['social_signals'].append(sig)
            seen_platforms.add(sig['platform'])

    # ── Clean up & score ─────────────────────────────────────────────────

    name = result['business_name']
    if name:
        name = name.strip()
        result['business_name'] = None if is_boilerplate(name) else name

    # Confidence score
    score = 0
    if result['business_name']:    score += 30
    if result['phones']:           score += 20
    if result['emails']:           score += 15
    if addr['street']:             score += 15
    if addr['city']:               score += 5
    if result['social_signals']:   score += min(15, len(result['social_signals']) * 5)
    result['confidence_score'] = score

    return result


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    try:
        payload = json.loads(sys.stdin.read())
        html    = payload.get('html', '')
        url     = payload.get('url', '')
        domain  = payload.get('domain', '')
        out     = extract(html, url, domain)
        print(json.dumps(out))
    except Exception as e:
        print(json.dumps({'error': str(e), 'confidence_score': 0}))


if __name__ == '__main__':
    main()
