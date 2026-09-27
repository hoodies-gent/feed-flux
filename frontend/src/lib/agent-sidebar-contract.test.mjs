import assert from 'node:assert/strict';
import test from 'node:test';

let sidebarContract = {};
try {
  sidebarContract = await import('./agent-sidebar-contract.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('agent empty state exposes read-only inbox prompts', () => {
  assert.equal(sidebarContract.AGENT_EMPTY_STATE_TITLE, 'Where would you like to start?');
  assert.equal(
    sidebarContract.AGENT_EMPTY_STATE_DESCRIPTION,
    'Tell me what you need help with in your inbox or a specific email.',
  );
  assert.deepEqual(sidebarContract.AGENT_SUGGESTED_PROMPTS, [
    'Show me what’s new',
    'Anything I need to act on?',
  ]);
});
