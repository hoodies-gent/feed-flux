export function getReplyDraftId(drafts, focusedDraftId) {
  if (focusedDraftId && drafts.some((draft) => draft.id === focusedDraftId)) {
    return focusedDraftId;
  }
  return drafts[0]?.id ?? null;
}

export function shouldShowDraftPane(draftsByEmailId, isOpen, isDraftRequested = false) {
  if (isDraftRequested) return true;
  return isOpen && Object.values(draftsByEmailId).some((drafts) => drafts.length > 0);
}
