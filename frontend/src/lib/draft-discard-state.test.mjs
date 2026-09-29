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

test('discard removal preserves the deleted draft index', () => {
  const drafts = [{ id: 1 }, { id: 2 }, { id: 3 }];

  assert.deepEqual(
    discardState.removeDraftForDiscard?.(drafts, 2),
    {
      discardedIndex: 1,
      drafts: [{ id: 1 }, { id: 3 }],
    },
  );
});

test('restoring a draft inserts it at its prior index without duplicates', () => {
  const restored = { id: 2, status: 'draft' };

  assert.deepEqual(
    discardState.restoreDraftAfterDiscard?.(
      [{ id: 1 }, { id: 2, status: 'discarded' }, { id: 3 }],
      restored,
      1,
    ),
    [{ id: 1 }, restored, { id: 3 }],
  );
});
