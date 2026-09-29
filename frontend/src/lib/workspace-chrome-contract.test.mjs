import assert from 'node:assert/strict';
import test from 'node:test';

let workspaceChromeContract = {};
try {
  workspaceChromeContract = await import('./workspace-chrome-contract.mjs');
} catch {
  // The first TDD run intentionally exercises the missing module.
}

test('global AI action and assistant panel have distinct labels', () => {
  assert.equal(workspaceChromeContract.AI_ACTION_LABEL, 'Ask AI');
  assert.equal(workspaceChromeContract.AI_PANEL_LABEL, 'Assistant');
});

test('assistant sidebar toggle changes action and icon with panel state', () => {
  assert.deepEqual(workspaceChromeContract.getAssistantSidebarToggle(false), {
    label: 'Open Assistant',
    icon: 'panel-right-open',
  });
  assert.deepEqual(workspaceChromeContract.getAssistantSidebarToggle(true), {
    label: 'Hide Assistant',
    icon: 'panel-right-close',
  });
});

test('assistant new chat action uses additive presentation', () => {
  assert.deepEqual(workspaceChromeContract.ASSISTANT_NEW_CHAT_ACTION, {
    label: 'New chat',
    icon: 'plus',
    visibleWhenEmpty: true,
  });
});

test('assistant toggle moves into the header only while expanded', () => {
  assert.deepEqual(workspaceChromeContract.getAssistantSidebarLayout(false), {
    showContent: false,
    togglePlacement: 'collapsed-sidebar',
    toggleEdgeInset: 4,
  });
  assert.deepEqual(workspaceChromeContract.getAssistantSidebarLayout(true), {
    showContent: true,
    togglePlacement: 'header',
    toggleEdgeInset: 4,
  });
});

test('email detail AI action retains an accessible contextual label', () => {
  assert.equal(workspaceChromeContract.EMAIL_AI_ACTION_LABEL, 'Ask AI about this email');
});

test('email detail reply action has a direct accessible label', () => {
  assert.equal(workspaceChromeContract.EMAIL_REPLY_ACTION_LABEL, 'Reply');
});

test('collapsed assistant uses the same six-pixel gutter as horizontal panels', () => {
  assert.equal(workspaceChromeContract.WORKSPACE_PANEL_GAP_CLASS, 'gap-1.5');
});
