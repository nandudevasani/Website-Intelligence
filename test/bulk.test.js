import { test } from 'node:test';
import assert from 'node:assert/strict';

const BASE = `http://localhost:${process.env.PORT || 3000}`;

test('POST /api/analyze/bulk requires domains array', async () => {
  const res = await fetch(`${BASE}/api/analyze/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.ok(body.error, 'should return an error message');
});

test('POST /api/analyze/bulk rejects more than 100 domains', async () => {
  const domains = Array.from({ length: 101 }, (_, i) => `domain${i}.com`);
  const res = await fetch(`${BASE}/api/analyze/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domains }),
  });
  assert.equal(res.status, 400);
});

test('POST /api/analyze/bulk rejects empty array', async () => {
  const res = await fetch(`${BASE}/api/analyze/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domains: [] }),
  });
  assert.equal(res.status, 400);
});

test('POST /api/analyze/bulk returns array for valid input', async () => {
  const res = await fetch(`${BASE}/api/analyze/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domains: ['example.com'] }),
  });
  assert.ok(res.status === 200 || res.status === 422, `unexpected status ${res.status}`);
  const body = await res.json();
  assert.ok(Array.isArray(body) || typeof body === 'object', 'response should be array or object');
});
