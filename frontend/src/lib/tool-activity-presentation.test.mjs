import assert from 'node:assert/strict';
import test from 'node:test';

import { getToolActivityPresentation } from './tool-activity-presentation.mjs';

const toolLabels = [
  ['send_test_email', 'Saving test email', 'Saved test email'],
  ['find_email', 'Finding emails', 'Found emails'],
  ['read_calendar', 'Checking calendar', 'Checked calendar'],
  ['save_reply_draft', 'Saving reply draft', 'Saved reply draft'],
  ['apply_draft_patch', 'Updating draft', 'Updated draft'],
  ['read_draft_context', 'Reading draft', 'Read draft'],
  ['read_original_email_context', 'Reading email', 'Read email'],
  ['apply_triage_batch', 'Preparing inbox plan', 'Prepared inbox plan'],
  ['remember_memory', 'Saving to memory', 'Saved to memory'],
  ['list_memories', 'Checking memory', 'Checked memory'],
  ['update_memory', 'Updating memory', 'Updated memory'],
  ['forget_memory', 'Removing from memory', 'Removed from memory'],
  ['reset_memories', 'Clearing memory', 'Cleared memory'],
  ['record_memory_candidate', 'Reviewing memory suggestion', 'Reviewed memory suggestion'],
];

test('current agent tools use human-readable running and completed labels', () => {
  for (const [tool, runningLabel, completedLabel] of toolLabels) {
    assert.equal(
      getToolActivityPresentation({ tool, running: true }).label,
      runningLabel,
    );
    assert.equal(
      getToolActivityPresentation({ tool, running: false }).label,
      completedLabel,
    );
  }
});

test('inbox listing labels reflect overview, attention, and triage intent', () => {
  assert.equal(
    getToolActivityPresentation({
      tool: 'list_inbox_emails',
      running: true,
      args: { scope: 'recent', purpose: 'overview', limit: 20 },
    }).label,
    'Reviewing inbox',
  );
  assert.equal(
    getToolActivityPresentation({
      tool: 'list_inbox_emails',
      running: true,
      args: { scope: 'unread', purpose: 'attention', limit: 20 },
    }).label,
    'Finding what needs attention',
  );
  assert.equal(
    getToolActivityPresentation({
      tool: 'list_inbox_emails',
      running: false,
      args: { scope: 'recent', purpose: 'triage', limit: 20 },
    }).label,
    'Prepared inbox plan',
  );
});

test('bounded email results stay useful without exposing raw tool output', () => {
  assert.deepEqual(
    getToolActivityPresentation({
      tool: 'list_inbox_emails',
      running: false,
      resultCount: 3,
      output: 'private raw output',
    }),
    { label: 'Reviewed inbox', summary: '3 emails' },
  );
  assert.equal(
    getToolActivityPresentation({
      tool: 'find_email',
      running: false,
      resultCount: 0,
    }).summary,
    'No matching emails',
  );
  assert.equal(
    getToolActivityPresentation({
      tool: 'list_inbox_emails',
      running: false,
      resultCount: 0,
    }).summary,
    'No emails',
  );
});

test('unknown tools use a generic collapsed label instead of their internal name', () => {
  assert.deepEqual(
    getToolActivityPresentation({
      tool: 'internal_future_tool',
      running: true,
    }),
    { label: 'Working', summary: '' },
  );
  assert.deepEqual(
    getToolActivityPresentation({
      tool: 'internal_future_tool',
      running: false,
    }),
    { label: 'Completed', summary: '' },
  );
});
