import assert from 'node:assert/strict';
import test from 'node:test';

let notificationPolicy = {};
try {
  notificationPolicy = await import('./notification-policy.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('global notifications appear at the bottom center', () => {
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.position, 'bottom-center');
});

test('ordinary notifications are brief and dismissible', () => {
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.duration, 3000);
  assert.equal(notificationPolicy.TOASTER_OPTIONS?.closeButton, true);
});

test('triage action notifications use an action label and colon', () => {
  assert.equal(
    notificationPolicy.formatTriageActionToast?.('mark_read', 'Weekly update'),
    'Mark as read: Weekly update',
  );
  assert.equal(
    notificationPolicy.formatTriageActionToast?.('archive', 'Weekly update'),
    'Archive: Weekly update',
  );
  assert.equal(
    notificationPolicy.formatTriageActionToast?.('delete', 'Weekly update'),
    'Delete: Weekly update',
  );
});
