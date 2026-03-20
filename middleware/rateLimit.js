/**
 * In-memory rate limiter middleware for Website Intelligence API.
 * No external dependencies required.
 *
 * Usage:
 *   import { createRateLimiter } from './middleware/rateLimit.js';
 *   app.use('/api/', createRateLimiter({ windowMs: 60_000, max: 100 }));
 */

/**
 * @param {{ windowMs?: number, max?: number }} options
 *   windowMs - sliding window duration in milliseconds (default: 60 000 = 1 min)
 *   max      - maximum requests per window per IP (default: 100)
 */
export function createRateLimiter({ windowMs = 60_000, max = 100 } = {}) {
  // Map<ip, number[]> — stores timestamps of recent requests
  const hits = new Map();

  // Purge stale entries every windowMs to prevent unbounded memory growth
  setInterval(() => {
    const cutoff = Date.now() - windowMs;
    for (const [ip, timestamps] of hits) {
      const recent = timestamps.filter(t => t > cutoff);
      if (recent.length === 0) hits.delete(ip);
      else hits.set(ip, recent);
    }
  }, windowMs).unref();

  return function rateLimitMiddleware(req, res, next) {
    const ip = req.ip || req.socket?.remoteAddress || 'unknown';
    const now = Date.now();
    const cutoff = now - windowMs;

    const timestamps = (hits.get(ip) || []).filter(t => t > cutoff);
    timestamps.push(now);
    hits.set(ip, timestamps);

    const remaining = Math.max(0, max - timestamps.length);
    const resetAt = Math.min(...timestamps) + windowMs;

    res.setHeader('X-RateLimit-Limit', max);
    res.setHeader('X-RateLimit-Remaining', remaining);
    res.setHeader('X-RateLimit-Reset', Math.ceil(resetAt / 1000));

    if (timestamps.length > max) {
      return res.status(429).json({
        error: 'Too many requests. Please slow down.',
        retryAfter: Math.ceil((resetAt - now) / 1000),
      });
    }

    next();
  };
}
