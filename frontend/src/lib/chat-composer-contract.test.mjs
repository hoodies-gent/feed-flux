import assert from 'node:assert/strict';
import test from 'node:test';

let chatComposerContract = {};
try {
  chatComposerContract = await import('./chat-composer-contract.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('chat composer submits on Enter but preserves Shift+Enter for new lines', () => {
  assert.equal(chatComposerContract.shouldSubmitChatInput({ key: 'Enter', shiftKey: false }), true);
  assert.equal(chatComposerContract.shouldSubmitChatInput({ key: 'Enter', shiftKey: true }), false);
  assert.equal(chatComposerContract.shouldSubmitChatInput({ key: 'a', shiftKey: false }), false);
});
