export const DRAFT_FOCUS_LAYOUT = {
  'email-body-panel': 25,
  'email-action-panel': 75,
};

export function isDraftFocusLayout(layout) {
  return layout?.['email-action-panel'] >= 70;
}

export function enterDraftFocus() {
  return { ...DRAFT_FOCUS_LAYOUT };
}
