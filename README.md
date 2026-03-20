# Website Intelligence

A domain and website analysis engine that detects parked domains, shell sites, cross-domain redirects, coming-soon pages, political campaigns, and extracts structured business contact information.

## Features

- **Domain Analysis** — DNS records (A, MX, NS, CNAME, TXT), SSL/TLS inspection, HTTP status
- **Content Classification** — Detects parked, shell, coming-soon, blocked, redirect, and error pages
- **Business Extraction** — Pulls business name, address, phone, email, and social media from live sites
- **Bulk Processing** — Analyze up to 100 domains in parallel with progress tracking
- **Domain Age** — Looks up registration date and registrar via multiple WHOIS sources

## Requirements

- Node.js >= 18.0.0
- Python >= 3.10.0

## Quick Start

```bash
# Install dependencies
npm install
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server
npm start        # production
npm run dev      # development (auto-reload)
```

Server runs at `http://localhost:3000` by default.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3000` | HTTP port to listen on |
| `NODE_ENV` | `development` | `development` or `production` |
| `PYTHON_EXTRACTOR_CMD` | `python3` | Python interpreter to use |

See `.env.example` for a full template.

## API Reference

### `POST /api/analyze`

Analyze a single domain.

**Request body:**
```json
{ "domain": "example.com" }
```

**Response:**
```json
{
  "domain": "example.com",
  "httpStatus": 200,
  "responseTime": 412,
  "finalUrl": "https://example.com/",
  "verdict": "VALID",
  "reasons": [],
  "ssl": { "valid": true, "issuer": "Let's Encrypt", "daysRemaining": 82 },
  "dns": { "aRecords": ["93.184.216.34"], "mxRecords": [], "nsRecords": [...] }
}
```

### `POST /api/analyze/bulk`

Analyze up to 100 domains in parallel.

**Request body:**
```json
{ "domains": ["example.com", "google.com"] }
```

**Response:** Array of single-domain analysis results.

### `POST /api/extract-business`

Extract structured business information from a website.

**Request body:**
```json
{ "domain": "example.com" }
```

**Response:**
```json
{
  "domain": "example.com",
  "websiteStatus": "VALID",
  "business": {
    "businessName": "Example Corp",
    "phones": ["+1 555-000-0000"],
    "emails": ["info@example.com"],
    "address": { "street": "123 Main St", "city": "Anytown", "state": "CA", "zip": "90210" },
    "socialSignals": [{ "platform": "facebook", "url": "..." }],
    "strength": { "score": 72, "confidence": "high" }
  }
}
```

### `GET /api/domain-age?domain=example.com`

Get domain registration date and registrar.

### `GET /api/health`

Returns server uptime and version.

## Content Verdicts

| Verdict | Meaning |
|---|---|
| `VALID` | Legitimate active website |
| `PARKED` | Parked domain placeholder |
| `SHELL_SITE` | Empty template shell |
| `COMING_SOON` | Coming soon / under construction |
| `NO_CONTENT` | Empty or no visible content |
| `CROSS_DOMAIN_REDIRECT` | Redirects to a different domain |
| `POLITICAL_CAMPAIGN` | Political campaign site |
| `BLOCKED` | Cloudflare or WAF blocked |
| `DEFAULT_PAGE` | Default server / hosting page |
| `ERROR` | HTTP or network error |

## Business Extraction Confidence

Business name confidence tiers (highest to lowest):

| Confidence | Source |
|---|---|
| 95% | Schema.org JSON-LD `LocalBusiness` |
| 88% | `og:site_name` meta tag |
| 85% | `itemprop="name"` microdata or footer copyright |
| 82% | `application-name` meta tag |
| 80% | `apple-mobile-web-app-title` meta tag |
| 78% | `og:title` meta tag |
| 70% | `<title>` or `<h1>` tag |

## Deployment

The project is configured for [Render.com](https://render.com) via `render.yaml`.

```bash
# Build
npm install && pip install -r requirements.txt

# Start
npm start
```

Required env vars on Render: `NODE_ENV=production`, `PORT=3000`, `PYTHON_EXTRACTOR_CMD=python3`

## Running Tests

```bash
npm test
```

Tests use Node.js built-in `node:test` runner — no extra packages required.

## Project Structure

```
website-intelligence/
├── server.js                          # Express backend, all API routes
├── index.html                         # Single-page frontend
├── extractor.py                       # Python business extractor (primary)
├── business_extraction_helper.py      # Address / name parsing helpers
├── fallback_business_extractor.py     # BeautifulSoup fallback extractor
├── python_business_extractor_bridge.py # stdin/stdout JSON bridge
├── middleware/
│   ├── validate.js                    # Request body validation
│   └── rateLimit.js                   # In-memory rate limiter
├── test/
│   ├── analyze.test.js                # /api/analyze endpoint tests
│   ├── bulk.test.js                   # /api/analyze/bulk tests
│   └── health.test.js                 # /api/health tests
├── public/                            # Static assets (served by Express)
├── .env.example                       # Environment variable template
├── package.json
├── requirements.txt
└── render.yaml
```

## License

MIT
