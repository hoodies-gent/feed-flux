import assert from 'node:assert/strict';
import test from 'node:test';

import * as inlineCitations from './inline-email-citations.mjs';
import {
  getInlineCitationKey,
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

const mentionReferences = [
  {
    citation_key: 'inbox-1',
    email_id: 'email-1',
    sender: 'Rohit Kumar',
    subject: 'Please review: PR #412',
  },
  {
    citation_key: 'inbox-2',
    email_id: 'email-2',
    sender: 'Marcus Patel',
    subject: 'Reschedule: Atlas migration deep-dive → Thu 14:00',
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

test('completed responses cite an exact unique subject without breaking surrounding markdown', () => {
  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      '最紧急的是 **Rohit Kumar 的 "Please review: PR #412"**。',
      mentionReferences,
      false,
    ),
    '最紧急的是 **Rohit Kumar 的 "Please review: PR #412"**。<!--feedflux_ref:inbox-1-->',
  );
});

test('existing inline citations are preserved without a duplicate fallback', () => {
  const content = 'Please review: PR #412<!--feedflux_ref:inbox-1--> needs attention.';

  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(content, mentionReferences, false),
    content,
  );
});

test('visible citation keys for known sources become one inline citation', () => {
  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      'Rohit Kumar（inbox-1） needs attention.<!--feedflux_ref:inbox-1-->',
      mentionReferences,
      false,
    ),
    'Rohit Kumar<!--feedflux_ref:inbox-1--> needs attention.',
  );
});

test('a visible group of known citation keys becomes an inline avatar sequence', () => {
  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      'Low signal: GitHub（inbox-1、inbox-2）.<!--feedflux_ref:inbox-1-->',
      mentionReferences,
      false,
    ),
    'Low signal: GitHub<!--feedflux_ref:inbox-1--><!--feedflux_ref:inbox-2-->.',
  );
});

test('a citation-like group containing an unknown key remains ordinary text', () => {
  const content = 'External references（inbox-1、inbox-99）.';

  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      content,
      mentionReferences,
      false,
    ),
    content,
  );
});

test('unknown parenthetical keys remain ordinary response text', () => {
  const content = 'The external reference is （inbox-99）.';

  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      content,
      mentionReferences,
      false,
    ),
    content,
  );
});

test('a unique sender mention is cited when the subject is absent', () => {
  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      'Marcus Patel asked to move the meeting.',
      mentionReferences,
      false,
    ),
    'Marcus Patel<!--feedflux_ref:inbox-2--> asked to move the meeting.',
  );
});

test('ambiguous sender and subject mentions are not guessed', () => {
  const duplicateSenderReferences = [
    mentionReferences[0],
    {
      citation_key: 'inbox-3',
      email_id: 'email-3',
      sender: 'Rohit Kumar',
      subject: 'Please review: PR #412',
    },
  ];
  const content = 'Rohit Kumar mentioned Please review: PR #412.';

  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(
      content,
      duplicateSenderReferences,
      false,
    ),
    content,
  );
});

test('streaming responses wait until completion before adding fallback citations', () => {
  const content = 'Marcus Patel asked to move the meeting.';

  assert.equal(
    inlineCitations.addMissingInlineEmailCitations(content, mentionReferences, true),
    content,
  );
});
