export function getDraftDiscardMode(confirmingDraftId, draftId) {
  return confirmingDraftId === draftId ? 'confirm' : 'idle';
}

export function removeDraftForDiscard(drafts, draftId) {
  return {
    discardedIndex: drafts.findIndex((draft) => draft.id === draftId),
    drafts: drafts.filter((draft) => draft.id !== draftId),
  };
}

export function restoreDraftAfterDiscard(drafts, restoredDraft, discardedIndex) {
  const nextDrafts = drafts.filter((draft) => draft.id !== restoredDraft.id);
  const insertAt = discardedIndex < 0
    ? nextDrafts.length
    : Math.min(discardedIndex, nextDrafts.length);
  nextDrafts.splice(insertAt, 0, restoredDraft);
  return nextDrafts;
}
