/**
 * Website Intelligence v4.0 — server.js
 * Architecture: Less regex, more DOM (Cheerio), Python handles all business extraction.
 *
 * Endpoints:
 *   GET  /api/health
 *   POST /api/analyze          → status, DNS, SSL, HTTP, domain age
 *   POST /api/extract-business → business name, address, phone, email, social
 */

import express from 'express';
import axios from 'axios';
import * as cheerio from 'cheerio';
import dns from 'dns/promises';
import tls from 'tls';
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;
const PY_CMD = process.env.PYTHON_EXTRACTOR_CMD || 'python3';

app.use(express.json({ limit: '4mb' }));
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  next();
});
app.use(express.static(__dirname));

// ─── Helpers ─────────────────────────────────────────────────────────────────

function normalizeDomain(input = '') {
  return input.trim().toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/\/.*$/, '')
    .replace(/^www\./, '');
}

function rootDomain(urlStr) {
  try {
    const host = new URL(urlStr).hostname.toLowerCase().replace(/^www\./, '');
    const parts = host.split('.');
    return parts.length >= 2 ? parts.slice(-2).join('.') : host;
  } catch { return ''; }
}

function formatAgeText(days) {
  if (days === null || days < 0) return null;
  const y = Math.floor(days / 365);
  const m = Math.floor((days % 365) / 30);
  if (y > 0 && m > 0) return `${y}Y ${m}M`;
  if (y > 0) return `${y}Y`;
  if (m > 0) return `${m}M`;
  return '< 1M';
}

// ─── DNS ─────────────────────────────────────────────────────────────────────

async function checkDns(domain) {
  const r = { hasARecord: false, aRecords: [], mxRecords: [], nsRecords: [] };
  await Promise.allSettled([
    dns.resolve4(domain).then(a  => { r.aRecords = a; r.hasARecord = a.length > 0; }),
    dns.resolveMx(domain).then(mx => { r.mxRecords = mx; }),
    dns.resolveNs(domain).then(ns => { r.nsRecords = ns; }),
  ]);
  return r;
}

// ─── SSL ─────────────────────────────────────────────────────────────────────

async function checkSsl(domain) {
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve({ valid: false, error: 'timeout' }), 7000);
    try {
      const sock = tls.connect(443, domain, { servername: domain, rejectUnauthorized: false }, () => {
        clearTimeout(timer);
        const cert = sock.getPeerCertificate();
        sock.destroy();
        if (!cert?.subject) return resolve({ valid: false });
        const exp = new Date(cert.valid_to);
        const days = Math.floor((exp - Date.now()) / 86400000);
        resolve({
          valid: days > 0,
          issuer: cert.issuer?.O || cert.issuer?.CN || null,
          daysRemaining: days,
          protocol: sock.getProtocol(),
          expiresAt: exp.toISOString(),
        });
      });
      sock.on('error', () => { clearTimeout(timer); resolve({ valid: false }); });
    } catch { clearTimeout(timer); resolve({ valid: false }); }
  });
}

// ─── HTTP ─────────────────────────────────────────────────────────────────────

async function checkHttp(url) {
  // Accept full URL or bare domain
  const urls = url.startsWith('http') ? [url] : [`https://${url}`, `http://${url}`];
  for (const u of urls) {
    try {
      const t0 = Date.now();
      const r = await axios.get(u, {
        timeout: 15000,
        maxRedirects: 5,
        validateStatus: () => true,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9',
        },
        maxContentLength: 3 * 1024 * 1024,
      });
      return {
        isUp: true,
        statusCode: r.status,
        statusText: r.statusText,
        finalUrl: r.request?.res?.responseUrl || u,
        html: typeof r.data === 'string' ? r.data : '',
        responseTime: Date.now() - t0,
        headers: {
          server: r.headers['server'] || null,
          poweredBy: r.headers['x-powered-by'] || null,
          contentType: r.headers['content-type'] || null,
        },
      };
    } catch (e) {
      if (e.code === 'ENOTFOUND' || e.code === 'ECONNREFUSED') break;
    }
  }
  return { isUp: false, statusCode: 0, statusText: '', finalUrl: '', html: '', responseTime: 0, headers: {} };
}

// ─── Status Detection (DOM-first via Cheerio) ────────────────────────────────

function detectStatus($, { statusCode, isUp, finalUrl, originalDomain }) {
  if (!isUp) return 'DOWN';

  // ── DOM signals ──
  const generator  = ($('meta[name="generator"]').attr('content') || '').toLowerCase();
  const titleText  = $('title').text().trim().toLowerCase();
  const bodyText   = $('body').text();
  const bodyLower  = bodyText.toLowerCase();
  const wordCount  = bodyText.split(/\s+/).filter(w => w.length > 2).length;

  // Structural presence (DOM selectors, no regex on raw HTML)
  const hasNav         = $('nav, [role="navigation"]').length > 0;
  const hasFooter      = $('footer').length > 0;
  const hasContactLink = $('a[href^="tel:"], a[href^="mailto:"]').length > 0;
  const hasForm        = $('form').length > 0;

  // ── CROSS_DOMAIN_REDIRECT ──
  if (finalUrl && originalDomain) {
    const finalRoot = rootDomain(finalUrl);
    if (finalRoot && finalRoot !== originalDomain && !finalRoot.endsWith('.' + originalDomain)) {
      return 'CROSS_DOMAIN_REDIRECT';
    }
  }

  // ── SUSPENDED: heading text check ──
  const suspendedHeading = $('h1, h2').filter((_, el) =>
    /account suspended|this account has been suspended|hosting suspended/i.test($(el).text())
  );
  if (suspendedHeading.length) return 'SUSPENDED';
  // Also check prominent class/id names (DOM)
  if ($('.suspended, #suspended, [class*="account-suspended"]').length) return 'SUSPENDED';

  // ── DEFAULT PAGE: Apache / Nginx / IIS ──
  const defaultHeading = $('h1').filter((_, el) =>
    /^it works!?$|apache.*default page|test page for nginx|welcome to nginx|iis windows server/i.test($(el).text().trim())
  );
  if (defaultHeading.length) return 'DEFAULT_PAGE';
  if (/test page for the nginx|welcome to nginx|apache2 ubuntu default/i.test(titleText)) return 'DEFAULT_PAGE';

  // ── BLOCKED: Cloudflare / WAF ──
  const hasCfDom = $('[class*="cf-error-"], [id*="cf-error"], [class*="cloudflare-error"], #challenge-body').length > 0;
  if ((statusCode === 403 && wordCount < 150) || hasCfDom) return 'BLOCKED';

  // ── PARKED ──
  const parkingGenerators = ['sedo', 'parkingcrew', 'dan.com', 'afternic', 'bodis', 'smartname', 'above.com'];
  if (parkingGenerators.some(g => generator.includes(g))) return 'PARKED';
  // DOM: parking service links
  if ($('a[href*="sedo.com"], a[href*="dan.com"], a[href*="afternic.com"], a[href*="parkingcrew.com"]').length) return 'PARKED';
  // DOM: parking class/id patterns
  if ($('[class*="parked-domain"], [id*="parked-domain"], [class*="domain-for-sale"]').length) return 'PARKED';
  // Last resort: key phrase in body text (via cheerio .text(), not raw HTML)
  const parkingPhrases = ['buy this domain', 'domain for sale', 'this domain is for sale', 'make an offer on this domain'];
  if (parkingPhrases.some(p => bodyLower.includes(p)) && wordCount < 300) return 'PARKED';

  // ── COMING SOON ──
  const hasComingSoonDom = $('[class*="coming-soon"], [id*="coming-soon"], [class*="maintenance"], [class*="countdown"], [class*="under-construction"]').length > 0;
  const comingSoonPhrases = ['coming soon', 'under construction', 'launching soon', "we'll be back", 'site is under maintenance', 'be right back'];
  if ((hasComingSoonDom || comingSoonPhrases.some(p => bodyLower.includes(p))) && wordCount < 400) return 'COMING_SOON';

  // ── SHELL SITE: website builder empty template ──
  const isBuilder = /wix\.com|squarespace|weebly|godaddy website builder/i.test(generator);
  const hasRealContent = (hasNav || hasFooter) && (hasContactLink || hasForm) && wordCount > 150;
  if (isBuilder && !hasRealContent) return 'SHELL_SITE';

  // ── NO CONTENT ──
  if (wordCount < 40) return 'NO_CONTENT';

  // ── ACTIVE ──
  if (statusCode >= 200 && statusCode < 400 && wordCount >= 100) return 'ACTIVE';
  if (isUp && wordCount >= 50 && hasRealContent) return 'ACTIVE';

  return 'ISSUES';
}

// Map overallStatus to frontend verdict/confidence/reasons/flags
function buildContentPayload($, overallStatus, finalUrl, originalDomain) {
  const statusToVerdict = {
    ACTIVE: 'VALID', PARKED: 'PARKED', COMING_SOON: 'COMING_SOON',
    NO_CONTENT: 'NO_CONTENT', DEFAULT_PAGE: 'DEFAULT_PAGE', SUSPENDED: 'SUSPENDED',
    SHELL_SITE: 'SHELL_SITE', CROSS_DOMAIN_REDIRECT: 'CROSS_DOMAIN_REDIRECT',
    BLOCKED: 'BLOCKED', DEAD: 'DEAD', DOWN: 'DOWN', ISSUES: 'ISSUES',
  };
  const reasonMap = {
    ACTIVE: 'Active website with meaningful content',
    PARKED: 'Domain is parked or listed for sale',
    COMING_SOON: 'Website is under construction or coming soon',
    NO_CONTENT: 'Page returned minimal or no content',
    DEFAULT_PAGE: 'Server default/test page detected',
    SUSPENDED: 'Account or hosting has been suspended',
    SHELL_SITE: 'Website builder template with no real business content',
    CROSS_DOMAIN_REDIRECT: 'Domain redirects to a different unrelated domain',
    BLOCKED: 'Access blocked by WAF or security service (e.g. Cloudflare)',
    DEAD: 'No DNS records found and site is unreachable',
    DOWN: 'Domain resolves but site is not responding',
    ISSUES: 'Site has content but could not be confidently classified',
  };

  // Confidence: DOM-based signals
  let confidence = 70;
  if (overallStatus === 'ACTIVE') {
    confidence = 50;
    if ($('nav').length) confidence += 15;
    if ($('a[href^="tel:"], a[href^="mailto:"]').length) confidence += 15;
    if ($('footer').length) confidence += 10;
    if ($('script[type="application/ld+json"]').length) confidence += 10;
    confidence = Math.min(95, confidence);
  } else if (['PARKED', 'SUSPENDED', 'DEFAULT_PAGE'].includes(overallStatus)) {
    confidence = 90;
  } else if (['DEAD', 'DOWN', 'BLOCKED'].includes(overallStatus)) {
    confidence = 95;
  }

  // Flags from DOM
  const flags = [];
  if (overallStatus === 'CROSS_DOMAIN_REDIRECT') flags.push('CROSS_DOMAIN_REDIRECT');
  if (overallStatus === 'PARKED') flags.push('PARKED');
  if (overallStatus === 'SHELL_SITE') flags.push('SHELL_SITE');
  if ($('script[type="application/ld+json"]').length) flags.push('HAS_SCHEMA_ORG');
  if ($('form[action*="login"], input[type="password"]').length) flags.push('WEB_APP_LOGIN');
  if ($('[class*="recaptcha"], [data-sitekey]').length) flags.push('HAS_RECAPTCHA');

  // DOM-based content details
  const bodyText = $('body').text();
  const words = bodyText.split(/\s+/).filter(w => w.length > 2);
  const uniqueWordCount = new Set(words.map(w => w.toLowerCase())).size;

  return {
    verdict: statusToVerdict[overallStatus] || overallStatus,
    confidence,
    reasons: [reasonMap[overallStatus] || overallStatus],
    flags,
    redirectInfo: overallStatus === 'CROSS_DOMAIN_REDIRECT'
      ? { source: originalDomain, target: finalUrl, method: 'HTTP redirect' }
      : null,
    details: {
      title: $('title').text().trim(),
      wordCount: words.length,
      uniqueWordCount,
      headings: $('h1, h2, h3').map((_, el) => $(el).text().trim()).get().filter(Boolean).slice(0, 5),
      images: $('img').length,
      links: {
        internal: $(`a[href^="/"], a[href*="${originalDomain}"]`).length,
        external: $('a[href^="http"]').not(`[href*="${originalDomain}"]`).length,
      },
      forms: $('form').length,
    },
  };
}

// ─── Domain Age (RDAP chain) ─────────────────────────────────────────────────

async function getDomainAge(domain) {
  const empty = { createdDate: null, ageInDays: null, ageText: null, registrar: null };

  function parseRdap(data) {
    const events = Array.isArray(data.events) ? data.events : [];
    const ev = events.find(e => /^registr|^creat/i.test(e.eventAction || ''));
    const raw = ev?.eventDate || data.creationDate || data.created;
    if (!raw) return null;
    const created = new Date(raw);
    if (isNaN(created.getTime())) return null;
    const days = Math.floor((Date.now() - created.getTime()) / 86400000);
    // Registrar from RDAP entities
    let registrar = null;
    for (const entity of data.entities || []) {
      if (entity.roles?.includes('registrar')) {
        const fn = entity.vcardArray?.[1]?.find(v => v[0] === 'fn');
        registrar = fn?.[3] || null;
        break;
      }
    }
    return { createdDate: created.toISOString().split('T')[0], ageInDays: days, ageText: formatAgeText(days), registrar };
  }

  const tld = domain.split('.').pop();
  // Ordered RDAP sources: direct registry first, then community proxies
  const sources = [];
  if (tld === 'com') sources.push(`https://rdap.verisign.com/com/v1/domain/${domain}`);
  if (tld === 'net') sources.push(`https://rdap.verisign.com/net/v1/domain/${domain}`);
  if (tld === 'org') sources.push(`https://rdap.publicinterestregistry.org/rdap/domain/${domain}`);
  sources.push(`https://rdap.org/domain/${domain}`, `https://rdapserver.net/domain/${domain}`);

  for (const url of sources) {
    try {
      const r = await axios.get(url, {
        timeout: 8000,
        headers: { Accept: 'application/rdap+json, application/json' },
        validateStatus: s => s === 200,
      });
      const parsed = parseRdap(r.data);
      if (parsed) return parsed;
    } catch { /* try next */ }
  }
  return empty;
}

// ─── Python Extractor ────────────────────────────────────────────────────────

function runPython(html, url, domain) {
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve(null), 35000);
    const py = spawn(PY_CMD, [path.join(__dirname, 'extractor.py')]);
    let out = '', err = '';
    py.stdout.on('data', d => out += d);
    py.stderr.on('data', d => err += d);
    py.on('close', () => {
      clearTimeout(timer);
      try { resolve(JSON.parse(out)); } catch { resolve(null); }
    });
    py.on('error', () => { clearTimeout(timer); resolve(null); });
    py.stdin.write(JSON.stringify({ html, url, domain }));
    py.stdin.end();
  });
}

// ─── Routes ─────────────────────────────────────────────────────────────────

app.get('/api/health', (_, res) => res.json({ ok: true, version: '4.0' }));

// ── Single domain analysis ──
app.post('/api/analyze', async (req, res) => {
  const domain = normalizeDomain(req.body?.domain || '');
  if (!domain) return res.status(400).json({ error: 'domain required' });

  try {
    // DNS + HTTP in parallel, then SSL + domain age in parallel
    const [dnsResult, httpResult] = await Promise.all([checkDns(domain), checkHttp(domain)]);
    const [sslResult, domainAge]  = await Promise.all([checkSsl(domain), getDomainAge(domain)]);

    const $ = cheerio.load(httpResult.html || '');

    // Overall status
    let overallStatus;
    if (!dnsResult.hasARecord && !httpResult.isUp) overallStatus = 'DEAD';
    else if (!httpResult.isUp) overallStatus = 'DOWN';
    else overallStatus = detectStatus($, {
      statusCode: httpResult.statusCode,
      isUp: httpResult.isUp,
      finalUrl: httpResult.finalUrl,
      originalDomain: domain,
    });

    const statusColors = {
      ACTIVE: 'green', DEAD: 'red', DOWN: 'red', PARKED: 'orange',
      COMING_SOON: 'yellow', NO_CONTENT: 'orange', DEFAULT_PAGE: 'orange',
      SUSPENDED: 'red', SHELL_SITE: 'orange', CROSS_DOMAIN_REDIRECT: 'red',
      BLOCKED: 'orange', ISSUES: 'yellow',
    };

    res.json({
      domain,
      timestamp: new Date().toISOString(),
      overallStatus,
      statusColor: statusColors[overallStatus] || 'gray',
      isGenuinelyValid: overallStatus === 'ACTIVE',
      dns: dnsResult,
      ssl: sslResult,
      http: {
        statusCode: httpResult.statusCode,
        statusText: httpResult.statusText,
        isUp: httpResult.isUp,
        finalUrl: httpResult.finalUrl,
        responseTime: httpResult.responseTime,
        headers: httpResult.headers,
      },
      content: buildContentPayload($, overallStatus, httpResult.finalUrl, domain),
      domainAge,
    });
  } catch (e) {
    res.status(500).json({ error: e.message, domain, overallStatus: 'ERROR' });
  }
});

// ── Business info extraction ──
app.post('/api/extract-business', async (req, res) => {
  const domain = normalizeDomain(req.body?.domain || '');
  if (!domain) return res.status(400).json({ error: 'domain required' });

  try {
    const [dnsResult, httpResult] = await Promise.all([checkDns(domain), checkHttp(domain)]);
    const $ = cheerio.load(httpResult.html || '');

    let websiteStatus;
    if (!dnsResult.hasARecord && !httpResult.isUp) websiteStatus = 'DEAD';
    else if (!httpResult.isUp) websiteStatus = 'DOWN';
    else websiteStatus = detectStatus($, {
      statusCode: httpResult.statusCode,
      isUp: httpResult.isUp,
      finalUrl: httpResult.finalUrl,
      originalDomain: domain,
    });

    const content = buildContentPayload($, websiteStatus, httpResult.finalUrl, domain);

    // Dead / unreachable / blocked: return minimal response
    if (['DEAD', 'DOWN', 'SUSPENDED', 'BLOCKED'].includes(websiteStatus)) {
      return res.json({
        domain, websiteStatus, reasons: content.reasons,
        business: {
          businessName: null, phones: [], emails: [],
          address: { street: '', city: '', state: '', zip: '' },
          socialSignals: [], metaDescription: null, businessType: null, confidence: 0,
        },
      });
    }

    // Run Python extractor on main page
    const pyMain = await runPython(httpResult.html, httpResult.finalUrl || `https://${domain}`, domain);

    // If still missing contact info, try /contact page
    const needsMore = !pyMain?.phones?.length || !pyMain?.address?.street;
    let pyContact = null;
    if (needsMore) {
      const contactUrls = [`https://${domain}/contact`, `https://${domain}/contact-us`];
      for (const cu of contactUrls) {
        try {
          const ch = await checkHttp(cu);
          if (ch.isUp && ch.html) {
            pyContact = await runPython(ch.html, ch.finalUrl, domain);
            break;
          }
        } catch { /* skip */ }
      }
    }

    // Merge contact page into main result
    const biz = pyMain || {};
    if (pyContact) {
      if (!biz.phones?.length  && pyContact.phones?.length)  biz.phones  = pyContact.phones;
      if (!biz.emails?.length  && pyContact.emails?.length)  biz.emails  = pyContact.emails;
      if (!biz.address?.street && pyContact.address?.street) biz.address = pyContact.address;
      // Merge social signals (no duplicates by platform)
      const seen = new Set((biz.social_signals || []).map(s => s.platform));
      for (const sig of pyContact.social_signals || []) {
        if (!seen.has(sig.platform)) { biz.social_signals = biz.social_signals || []; biz.social_signals.push(sig); seen.add(sig.platform); }
      }
    }

    res.json({
      domain,
      websiteStatus,
      reasons: content.reasons,
      business: {
        businessName:   biz.business_name   || null,
        phones:         biz.phones          || [],
        emails:         biz.emails          || [],
        address:        biz.address         || { street: '', city: '', state: '', zip: '' },
        socialSignals:  biz.social_signals  || [],
        metaDescription: biz.meta_description || null,
        businessType:   biz.business_type   || null,
        confidence:     biz.confidence_score || 0,
        country:        biz.country         || { code: 'US', name: 'United States' },
      },
    });
  } catch (e) {
    res.status(500).json({ error: e.message, domain, websiteStatus: 'ERROR' });
  }
});

app.listen(PORT, () => console.log(`Website Intelligence v4.0 on port ${PORT}`));
