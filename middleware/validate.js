/**
 * Request body validation middleware for Website Intelligence API.
 */

/**
 * Validates POST /api/analyze and POST /api/extract-business requests.
 * Expects: { domain: string }
 */
export function validateSingleDomain(req, res, next) {
  const { domain } = req.body || {};
  if (!domain) {
    return res.status(400).json({ error: 'domain is required' });
  }
  if (typeof domain !== 'string') {
    return res.status(400).json({ error: 'domain must be a string' });
  }
  const trimmed = domain.trim();
  if (trimmed.length === 0) {
    return res.status(400).json({ error: 'domain must not be empty' });
  }
  if (trimmed.length > 253) {
    return res.status(400).json({ error: 'domain exceeds maximum length' });
  }
  req.body.domain = trimmed;
  next();
}

/**
 * Validates POST /api/analyze/bulk requests.
 * Expects: { domains: string[] }  (1–100 items)
 */
export function validateBulkDomains(req, res, next) {
  const { domains } = req.body || {};
  if (!domains) {
    return res.status(400).json({ error: 'domains array is required' });
  }
  if (!Array.isArray(domains)) {
    return res.status(400).json({ error: 'domains must be an array' });
  }
  if (domains.length === 0) {
    return res.status(400).json({ error: 'domains array must not be empty' });
  }
  if (domains.length > 100) {
    return res.status(400).json({ error: 'domains array must not exceed 100 items' });
  }
  for (const d of domains) {
    if (typeof d !== 'string' || d.trim().length === 0) {
      return res.status(400).json({ error: 'each domain must be a non-empty string' });
    }
  }
  req.body.domains = domains.map(d => d.trim());
  next();
}
