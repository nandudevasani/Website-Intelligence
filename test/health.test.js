import { test } from 'node:test';
import assert from 'node:assert/strict';

const BASE = `http://localhost:${process.env.PORT || 3000}`;

test('GET /api/health returns status ok', async () => {
  const res = await fetch(`${BASE}/api/health`);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.status, 'ok');
  assert.ok(typeof body.uptime === 'number', 'uptime should be a number');
  assert.ok(typeof body.version === 'string', 'version should be a string');
});
