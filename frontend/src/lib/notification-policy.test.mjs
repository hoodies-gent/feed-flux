import assert from 'node:assert/strict';
import test from 'node:test';

let notificationPolicy = {};
try {
  notificationPolicy = await import('./notification-policy.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('global notifications stay clear of top actions and the agent composer', () => {
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.position, 'bottom-left');
});

test('ordinary notifications are brief and dismissible', () => {
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.duration, 3000);
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.closeButton, true);
});
