import assert from 'node:assert/strict';
import test from 'node:test';

import * as feedState from './feed-load-state.mjs';

test('feed load mode distinguishes the first read from later refreshes', () => {
  assert.equal(feedState.getFeedLoadMode(false), 'initial');
  assert.equal(feedState.getFeedLoadMode(true), 'background');
});

test('background failures keep an existing feed visible', () => {
  assert.equal(feedState.shouldRenderFeedError({ feedCount: 0, error: 'failed' }), true);
  assert.equal(feedState.shouldRenderFeedError({ feedCount: 20, error: 'failed' }), false);
  assert.equal(feedState.shouldRenderFeedError({ feedCount: 20, error: null }), false);
});
