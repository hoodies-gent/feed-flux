export const TOASTER_OPTIONS = {
  position: 'bottom-center',
  duration: 3000,
  closeButton: true,
  richColors: true,
};

const TRIAGE_ACTION_LABELS = {
  mark_read: 'Mark as read',
  archive: 'Archive',
  delete: 'Delete',
};

export function formatTriageActionToast(action, title) {
  return `${TRIAGE_ACTION_LABELS[action]}: ${title}`;
}
