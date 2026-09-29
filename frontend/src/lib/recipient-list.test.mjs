import assert from 'node:assert/strict';
import test from 'node:test';

import {
  commitRecipientInput,
  parseRecipientList,
  removeRecipient,
} from './recipient-list.mjs';

test('parses a normalized recipient list', () => {
  assert.deepEqual(
    parseRecipientList('one@example.com, two@example.com'),
    ['one@example.com', 'two@example.com'],
  );
});

test('commits multiple recipients and removes case-insensitive duplicates', () => {
  assert.deepEqual(
    commitRecipientInput('one@example.com', 'TWO@example.com, One@example.com'),
    {
      value: 'one@example.com, TWO@example.com',
      recipients: ['one@example.com', 'TWO@example.com'],
      invalid: [],
    },
  );
});

test('returns invalid entries without adding them to the recipient value', () => {
  assert.deepEqual(
    commitRecipientInput('one@example.com', 'two@example.com, not-an-email'),
    {
      value: 'one@example.com, two@example.com',
      recipients: ['one@example.com', 'two@example.com'],
      invalid: ['not-an-email'],
    },
  );
});

test('removes one recipient and preserves the normalized order', () => {
  assert.equal(
    removeRecipient('one@example.com, two@example.com', 'one@example.com'),
    'two@example.com',
  );
});
