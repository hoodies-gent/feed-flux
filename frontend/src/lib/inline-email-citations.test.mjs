import assert from 'node:assert/strict';
import test from 'node:test';

import {
  getInlineCitationKey,
  getInlineCitationVerticalAlignment,
  linkifyInlineEmailCitations,
  resolveInlineEmailCitation,
} from './inline-email-citations.mjs';

const references = [
  {
    citation_key: 'inbox-1',
    email_id: 'email-1',
    sender: 'Sarah Chen',
    subject: 'Q4 launch review and next steps',
  },
];

test('complete citation markers become inline markdown links without changing surrounding text', () => {
  assert.equal(
    linkifyInlineEmailCitations(
      'The launch review is due Friday.<!--feedflux_ref:inbox-1--> Next item.',
    ),
    'The launch review is due Friday.[citation](#feedflux-citation=inbox-1) Next item.',
  );
});

test('partial citation markers stay hidden while a streamed marker is incomplete', () => {
  assert.equal(
    linkifyInlineEmailCitations('The launch review is due Friday.<!--feedflux_ref:inbo'),
    'The launch review is due Friday.',
  );
});

test('inline citations align to the full text box without a fixed pixel offset', () => {
  assert.equal(getInlineCitationVerticalAlignment(), 'text-bottom');
});

test('citation links resolve only to references supplied by the agent response', () => {
  const href = '#feedflux-citation=inbox-1';

  assert.equal(getInlineCitationKey(href), 'inbox-1');
  assert.deepEqual(resolveInlineEmailCitation(href, references, false), {
    state: 'resolved',
    reference: references[0],
  });
  assert.deepEqual(
    resolveInlineEmailCitation('#feedflux-citation=inbox-99', references, true),
    { state: 'pending' },
  );
  assert.deepEqual(
    resolveInlineEmailCitation('#feedflux-citation=inbox-99', references, false),
    { state: 'hidden' },
  );
  assert.deepEqual(
    resolveInlineEmailCitation('https://example.com', references, false),
    { state: 'not-citation' },
  );
});
