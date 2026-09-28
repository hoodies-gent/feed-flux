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

test('current message references remain the first choice for inline citations', () => {
  const currentReferences = [{
    citation_key: 'inbox-1',
    email_id: 'current-email',
    sender: 'Current Sender',
    subject: 'Current subject',
  }];
  const messages = [
    { role: 'assistant', references: [{ email_id: 'older-email' }] },
    { role: 'assistant', references: currentReferences },
  ];

  assert.deepEqual(
    agentMessageState.getInlineCitationReferences(messages, 1),
    currentReferences,
  );
});

test('a follow-up without sources reuses the nearest prior reference set with its citation keys', () => {
  const priorReference = {
    citation_key: 'inbox-1',
    email_id: 'prior-email',
    sender: 'Marcus Patel',
    subject: 'Re: shadow-write flag design — any thoughts?',
  };
  const messages = [
    { role: 'assistant', references: [{ email_id: 'older-email' }] },
    { role: 'assistant', references: [priorReference] },
    { role: 'user', content: 'Which one needs a reply?' },
    { role: 'assistant', content: 'The shadow-write email.' },
  ];

  assert.deepEqual(
    agentMessageState.getInlineCitationReferences(messages, 3),
    [priorReference],
  );
  assert.equal(priorReference.citation_key, 'inbox-1');
});

test('a message without current or prior references has no citation candidates', () => {
  assert.deepEqual(
    agentMessageState.getInlineCitationReferences([
      { role: 'user', content: 'Hello' },
      { role: 'assistant', content: 'Hi' },
    ], 1),
    [],
  );
});
