import assert from 'node:assert/strict';
import test from 'node:test';

import { getAgentSourcesPresentation } from './agent-sources-state.mjs';

const references = [
  { email_id: 'email-1', sender: 'Sarah Chen', subject: 'First' },
  { email_id: 'email-2', sender: 'GitHub', subject: 'Second' },
  { email_id: 'email-1', sender: 'Sarah Chen', subject: 'Duplicate' },
  { email_id: 'email-3', sender: 'Linear', subject: 'Third' },
  { email_id: 'email-4', sender: 'Notion', subject: 'Fourth' },
  { email_id: 'email-5', sender: 'Zoom', subject: 'Fifth' },
];

test('agent references preserve first-seen email order and remove duplicates', () => {
  const presentation = getAgentSourcesPresentation(references);

  assert.deepEqual(
    presentation.references.map((reference) => reference.email_id),
    ['email-1', 'email-2', 'email-3', 'email-4', 'email-5'],
  );
  assert.equal(presentation.references[0].subject, 'First');
});

test('collapsed source avatars cap visible emails and report the overflow', () => {
  const presentation = getAgentSourcesPresentation(references, 4);

  assert.equal(presentation.sectionLabel, 'Sources:');
  assert.equal(presentation.countLabel, '5 emails');
  assert.deepEqual(
    presentation.avatarReferences.map((reference) => reference.email_id),
    ['email-1', 'email-2', 'email-3', 'email-4'],
  );
  assert.equal(presentation.overflowCount, 1);
});

test('a single source uses the singular email count', () => {
  const presentation = getAgentSourcesPresentation(references.slice(0, 1));

  assert.equal(presentation.countLabel, '1 email');
});
