export const AI_ACTION_LABEL = 'Ask AI';
export const AI_PANEL_LABEL = 'Assistant';
export const EMAIL_AI_ACTION_LABEL = 'Ask AI about this email';
export const WORKSPACE_PANEL_GAP_CLASS = 'gap-2';

export function getAssistantSidebarToggle(isOpen) {
  return isOpen
    ? { label: 'Hide Assistant', icon: 'panel-right-close' }
    : { label: 'Open Assistant', icon: 'panel-right-open' };
}

export function getAssistantSidebarLayout(isOpen) {
  return isOpen
    ? { showContent: true, togglePlacement: 'header' }
    : { showContent: false, togglePlacement: 'collapsed-sidebar' };
}
