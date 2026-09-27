import assert from 'node:assert/strict';
import test from 'node:test';

let agentMessageState = {};
try {
  agentMessageState = await import('./agent-message-state.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

const referencedMessage = {
  role: 'assistant',
  references: [{ email_id: 'dev-email-1' }],
};

test('assistant references stay hidden until the turn completes', () => {
  assert.equal(
    agentMessageState.shouldShowMessageReferences({ ...referencedMessage, isStreaming: true }),
    false,
  );
  assert.equal(
    agentMessageState.shouldShowMessageReferences({ ...referencedMessage, isStreaming: false }),
    true,
  );
  assert.equal(
    agentMessageState.shouldShowMessageReferences({ role: 'assistant', isStreaming: false }),
    false,
  );
});

test('restored chat messages clear stream state that cannot resume after refresh', () => {
  const persisted = [{
    id: 'assistant-1',
    role: 'assistant',
    content: 'Partial answer',
    isLoading: true,
    isStreaming: true,
    references: [{ email_id: 'dev-email-1' }],
  }];

  assert.deepEqual(agentMessageState.restoreChatMessageState(persisted), [{
    ...persisted[0],
    isLoading: false,
    isStreaming: false,
  }]);
  assert.equal(persisted[0].isStreaming, true);
});
