export function getDraftDiscardMode(confirmingDraftId, draftId) {
  return confirmingDraftId === draftId ? 'confirm' : 'idle';
}
