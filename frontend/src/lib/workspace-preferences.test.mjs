import assert from 'node:assert/strict';
import test from 'node:test';

import * as preferences from './workspace-preferences.mjs';

test('workspace preferences expose a versioned parser', () => {
  assert.equal(typeof preferences.parseWorkspacePreferences, 'function');
  assert.equal(preferences.WORKSPACE_PREFERENCES_VERSION, 1);
});

test('workspace preferences expose storage helpers', () => {
  assert.equal(typeof preferences.loadWorkspacePreferences, 'function');
  assert.equal(typeof preferences.saveWorkspacePreferences, 'function');
});

const now = Date.UTC(2026, 8, 26);
const defaults = {
  activeEmailId: null,
  isAgentOpen: false,
  mainLayoutOpen: {
    'feed-panel': 30,
    'detail-panel': 50,
    'chat-panel': 20,
  },
  mainLayoutClosed: {
    'feed-panel': 30,
    'detail-panel': 70,
  },
  detailLayout: {
    'email-body-panel': 78,
    'email-action-panel': 22,
  },
};

test('malformed, wrong-version, and expired preferences fall back to defaults', () => {
  const expired = JSON.stringify({
    version: 1,
    savedAt: now - (31 * 24 * 60 * 60 * 1000),
    preferences: { ...defaults, isAgentOpen: true },
  });
  const wrongVersion = JSON.stringify({
    version: 99,
    savedAt: now,
    preferences: { ...defaults, isAgentOpen: true },
  });

  assert.deepEqual(preferences.parseWorkspacePreferences('{broken', now), defaults);
  assert.deepEqual(preferences.parseWorkspacePreferences(wrongVersion, now), defaults);
  assert.deepEqual(preferences.parseWorkspacePreferences(expired, now), defaults);
});

test('valid preferences restore presentation state', () => {
  const stored = {
    version: 1,
    savedAt: now - 1_000,
    preferences: {
      activeEmailId: 'dev-email-12',
      isAgentOpen: true,
      mainLayoutOpen: {
        'feed-panel': 25,
        'detail-panel': 52,
        'chat-panel': 23,
      },
      mainLayoutClosed: {
        'feed-panel': 35,
        'detail-panel': 65,
      },
      detailLayout: {
        'email-body-panel': 64,
        'email-action-panel': 36,
      },
    },
  };

  assert.deepEqual(
    preferences.parseWorkspacePreferences(JSON.stringify(stored), now),
    stored.preferences,
  );
});

test('out-of-range panel layouts fall back to defaults', () => {
  const stored = {
    version: 1,
    savedAt: now,
    preferences: {
      ...defaults,
      activeEmailId: 'dev-email-12',
      mainLayoutOpen: {
        'feed-panel': 5,
        'detail-panel': 75,
        'chat-panel': 20,
      },
    },
  };

  assert.deepEqual(
    preferences.parseWorkspacePreferences(JSON.stringify(stored), now),
    defaults,
  );
});

test('storage helpers persist only workspace presentation state', () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const presentation = {
    ...defaults,
    activeEmailId: 'dev-email-12',
    isAgentOpen: true,
    emailBody: 'must not be persisted',
    subject: 'must not be persisted',
  };
  const expectedPresentation = {
    ...defaults,
    activeEmailId: 'dev-email-12',
    isAgentOpen: true,
  };

  assert.equal(
    preferences.saveWorkspacePreferences(storage, presentation, now),
    true,
  );
  const serialized = values.get(preferences.WORKSPACE_PREFERENCES_STORAGE_KEY);
  const envelope = JSON.parse(serialized);
  assert.deepEqual(Object.keys(envelope).sort(), ['preferences', 'savedAt', 'version']);
  assert.deepEqual(
    Object.keys(envelope.preferences).sort(),
    [
      'activeEmailId',
      'detailLayout',
      'isAgentOpen',
      'mainLayoutClosed',
      'mainLayoutOpen',
    ],
  );
  assert.deepEqual(
    preferences.loadWorkspacePreferences(storage, now),
    expectedPresentation,
  );
});

test('storage failures safely return defaults', () => {
  const failingStorage = {
    getItem: () => {
      throw new Error('storage unavailable');
    },
    setItem: () => {
      throw new Error('storage unavailable');
    },
  };

  assert.deepEqual(
    preferences.loadWorkspacePreferences(failingStorage, now),
    defaults,
  );
  assert.equal(
    preferences.saveWorkspacePreferences(failingStorage, defaults, now),
    false,
  );
});
