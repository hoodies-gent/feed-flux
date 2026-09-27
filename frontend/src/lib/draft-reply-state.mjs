export function getReplyDraftId(drafts, focusedDraftId) {
  if (focusedDraftId && drafts.some((draft) => draft.id === focusedDraftId)) {
    return focusedDraftId;
  }
  return drafts[0]?.id ?? null;
}
