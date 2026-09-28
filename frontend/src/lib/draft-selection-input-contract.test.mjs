import assert from 'node:assert/strict';
import test from 'node:test';

let contract = {};
try {
  contract = await import('./draft-selection-input-contract.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('selection rewrite prompt opts out of browser address autofill', () => {
  assert.deepEqual(contract.DRAFT_SELECTION_PROMPT_INPUT_PROPS, {
    type: 'text',
    name: 'draft-selection-rewrite-instruction',
    autoComplete: 'off',
    'aria-label': 'Rewrite selected draft text',
  });
});
