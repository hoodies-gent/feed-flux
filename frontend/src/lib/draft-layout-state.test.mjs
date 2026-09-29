import assert from 'node:assert/strict';
import test from 'node:test';

let draftLayoutState = {};
try {
  draftLayoutState = await import('./draft-layout-state.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

const readingLayout = {
  'email-body-panel': 62,
  'email-action-panel': 38,
};

test('entering draft focus returns the compose-first layout', () => {
  assert.deepEqual(
    draftLayoutState.enterDraftFocus(readingLayout),
    {
      'email-body-panel': 25,
      'email-action-panel': 75,
    },
  );
});

test('entering draft focus is idempotent for an already focused layout', () => {
  assert.deepEqual(
    draftLayoutState.enterDraftFocus({
      'email-body-panel': 25,
      'email-action-panel': 75,
    }),
    {
      'email-body-panel': 25,
      'email-action-panel': 75,
    },
  );
});

test('draft focus is derived from the visible draft share', () => {
  assert.equal(
    draftLayoutState.isDraftFocusLayout({
      'email-body-panel': 25,
      'email-action-panel': 75,
    }),
    true,
  );
  assert.equal(draftLayoutState.isDraftFocusLayout(readingLayout), false);
});
