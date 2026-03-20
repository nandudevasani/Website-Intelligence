import { test } from 'node:test';
import assert from 'node:assert/strict';

const BASE = `http://localhost:${process.env.PORT || 3000}`;

test('POST /api/analyze requires domain field', async () => {
  const res = await fetch(`${BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  assert.equal(res.status, 400);
  const body = await res.json();
  assert.ok(body.error, 'should return an error message');
});

test('POST /api/analyze returns expected fields for a valid domain', async () => {
  const res = await fetch(`${BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domain: 'example.com' }),
  });
  assert.ok(res.status === 200 || res.status === 422, `unexpected status ${res.status}`);
  const body = await res.json();
  assert.ok('domain' in body, 'response should include domain');
  assert.ok('verdict' in body || 'error' in body, 'response should include verdict or error');
});

test('POST /api/analyze rejects non-string domain', async () => {
  const res = await fetch(`${BASE}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ domain: 12345 }),
  });
  assert.equal(res.status, 400);
});
