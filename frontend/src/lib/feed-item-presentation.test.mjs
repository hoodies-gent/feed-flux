import assert from 'node:assert/strict';
import test from 'node:test';

let feedItemPresentation = {};
try {
  feedItemPresentation = await import('./feed-item-presentation.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('active feed item keeps a persistent selection treatment', () => {
  assert.equal(
    feedItemPresentation.getFeedItemStateClassName(true),
    'border-b border-b-border border-l-[3px] border-l-primary bg-foreground/[0.11]',
  );
});

test('inactive feed item uses a background-only hover treatment', () => {
  assert.equal(
    feedItemPresentation.getFeedItemStateClassName(false),
    'border-b border-b-border border-l-[3px] border-l-transparent hover:bg-foreground/[0.06]',
  );
});
