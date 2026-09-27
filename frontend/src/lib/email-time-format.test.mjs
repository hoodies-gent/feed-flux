import assert from 'node:assert/strict';
import test from 'node:test';

let emailTimeFormat = {};
try {
  emailTimeFormat = await import('./email-time-format.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('email detail timestamp follows locale and omits seconds', () => {
  assert.equal(
    emailTimeFormat.formatEmailDateTime(1788840000, {
      locale: 'en-US',
      timeZone: 'UTC',
    }),
    'Sep 8, 2026, 4:00 AM',
  );
});

test('invalid email timestamps render no misleading date', () => {
  assert.equal(emailTimeFormat.formatEmailDateTime(Number.NaN), '');
});
