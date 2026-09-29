export const WORKSPACE_PREFERENCES_VERSION = 1;
export const WORKSPACE_PREFERENCES_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
export const WORKSPACE_PREFERENCES_STORAGE_KEY = 'feedflux_workspace_presentation_v1';

export const DEFAULT_WORKSPACE_PREFERENCES = {
  activeEmailId: null,
  isAgentOpen: false,
  isDraftPaneOpen: true,
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

const layoutRules = {
  mainLayoutOpen: {
    'feed-panel': [22, 50],
    'detail-panel': [32, 72],
    'chat-panel': [18, 38],
  },
  mainLayoutClosed: {
    'feed-panel': [22, 50],
    'detail-panel': [32, 72],
  },
  detailLayout: {
    'email-body-panel': [20, 88],
    'email-action-panel': [12, 80],
  },
};

function cloneDefaults() {
  return {
    ...DEFAULT_WORKSPACE_PREFERENCES,
    mainLayoutOpen: { ...DEFAULT_WORKSPACE_PREFERENCES.mainLayoutOpen },
    mainLayoutClosed: { ...DEFAULT_WORKSPACE_PREFERENCES.mainLayoutClosed },
    detailLayout: { ...DEFAULT_WORKSPACE_PREFERENCES.detailLayout },
  };
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isValidLayout(value, rules) {
  if (!isRecord(value)) return false;
  const expectedKeys = Object.keys(rules);
  if (
    Object.keys(value).length !== expectedKeys.length
    || !expectedKeys.every((key) => Object.hasOwn(value, key))
  ) {
    return false;
  }
  const total = expectedKeys.reduce((sum, key) => {
    const size = value[key];
    const [minimum, maximum] = rules[key];
    if (!Number.isFinite(size) || size < minimum || size > maximum) {
      return Number.NaN;
    }
    return sum + size;
  }, 0);
  return Number.isFinite(total) && Math.abs(total - 100) < 0.5;
}

export function parseWorkspacePreferences(raw, now = Date.now()) {
  const fallback = cloneDefaults();
  if (typeof raw !== 'string' || !raw) return fallback;

  try {
    const envelope = JSON.parse(raw);
    if (
      !isRecord(envelope)
      || envelope.version !== WORKSPACE_PREFERENCES_VERSION
      || !Number.isFinite(envelope.savedAt)
      || envelope.savedAt > now
      || now - envelope.savedAt > WORKSPACE_PREFERENCES_MAX_AGE_MS
      || !isRecord(envelope.preferences)
    ) {
      return fallback;
    }

    const stored = envelope.preferences;
    const activeEmailId = stored.activeEmailId;
    const isDraftPaneOpen = stored.isDraftPaneOpen ?? true;
    if (
      !(activeEmailId === null
        || (typeof activeEmailId === 'string'
          && activeEmailId.length > 0
          && activeEmailId.length <= 512))
      || typeof stored.isAgentOpen !== 'boolean'
      || typeof isDraftPaneOpen !== 'boolean'
      || !isValidLayout(stored.mainLayoutOpen, layoutRules.mainLayoutOpen)
      || !isValidLayout(stored.mainLayoutClosed, layoutRules.mainLayoutClosed)
      || !isValidLayout(stored.detailLayout, layoutRules.detailLayout)
    ) {
      return fallback;
    }

    return {
      activeEmailId,
      isAgentOpen: stored.isAgentOpen,
      isDraftPaneOpen,
      mainLayoutOpen: { ...stored.mainLayoutOpen },
      mainLayoutClosed: { ...stored.mainLayoutClosed },
      detailLayout: { ...stored.detailLayout },
    };
  } catch {
    return fallback;
  }
}

export function loadWorkspacePreferences(storage, now = Date.now()) {
  try {
    return parseWorkspacePreferences(
      storage.getItem(WORKSPACE_PREFERENCES_STORAGE_KEY),
      now,
    );
  } catch {
    return cloneDefaults();
  }
}

export function saveWorkspacePreferences(storage, presentation, now = Date.now()) {
  try {
    const candidate = JSON.stringify({
      version: WORKSPACE_PREFERENCES_VERSION,
      savedAt: now,
      preferences: presentation,
    });
    const normalized = parseWorkspacePreferences(candidate, now);
    storage.setItem(
      WORKSPACE_PREFERENCES_STORAGE_KEY,
      JSON.stringify({
        version: WORKSPACE_PREFERENCES_VERSION,
        savedAt: now,
        preferences: normalized,
      }),
    );
    return true;
  } catch {
    return false;
  }
}
