import assert from 'node:assert/strict';
import test from 'node:test';

let discardState = {};
try {
  discardState = await import('./draft-discard-state.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('only the selected draft enters discard confirmation', () => {
  assert.equal(discardState.getDraftDiscardMode?.(null, 1), 'idle');
  assert.equal(discardState.getDraftDiscardMode?.(1, 1), 'confirm');
  assert.equal(discardState.getDraftDiscardMode?.(2, 1), 'idle');
});
