import assert from 'node:assert/strict';
import test from 'node:test';

let draftReplyState = {};
try {
  draftReplyState = await import('./draft-reply-state.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

const drafts = [{ id: 4 }, { id: 7 }];

test('reply reuses the focused draft when it still exists', () => {
  assert.equal(draftReplyState.getReplyDraftId(drafts, 7), 7);
});

test('reply falls back to an existing draft instead of creating another', () => {
  assert.equal(draftReplyState.getReplyDraftId(drafts, 99), 4);
});

test('reply requests creation only when no draft exists', () => {
  assert.equal(draftReplyState.getReplyDraftId([], null), null);
});

test('draft pane visibility follows whether any actionable draft exists', () => {
  assert.equal(draftReplyState.shouldShowDraftPane({}), false);
  assert.equal(draftReplyState.shouldShowDraftPane({ email1: [] }), false);
  assert.equal(draftReplyState.shouldShowDraftPane({ email1: [], email2: [{ id: 4 }] }), true);
});
