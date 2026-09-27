import assert from 'node:assert/strict';
import test from 'node:test';

let headerContract = {};
try {
  headerContract = await import('./app-header-contract.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('header search copy promises keyword email search only', () => {
  assert.equal(
    headerContract.APP_HEADER_SEARCH_PLACEHOLDER,
    'Search emails by keyword, or type a question and click Ask AI',
  );
});

test('header search input opts out of browser address autofill', () => {
  assert.deepEqual(headerContract.APP_HEADER_SEARCH_INPUT_PROPS, {
    name: 'email-search',
    autoComplete: 'off',
    'aria-label': 'Search emails',
  });
});

test('Ask AI receives the trimmed search input as a prompt', () => {
  assert.equal(headerContract.getAskAiPrompt('  roadmap update  '), 'roadmap update');
  assert.equal(headerContract.getAskAiPrompt('   '), '');
});

test('low-frequency header actions use a neutral More label', () => {
  assert.equal(headerContract.APP_HEADER_MORE_LABEL, 'More');
});
