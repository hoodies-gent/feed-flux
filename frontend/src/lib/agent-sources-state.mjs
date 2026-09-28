export const MAX_VISIBLE_SOURCE_AVATARS = 4;

export function getAgentSourcesPresentation(
  references,
  maxVisibleAvatars = MAX_VISIBLE_SOURCE_AVATARS,
) {
  const seenEmailIds = new Set();
  const uniqueReferences = [];

  for (const reference of references) {
    if (!reference?.email_id || seenEmailIds.has(reference.email_id)) continue;
    seenEmailIds.add(reference.email_id);
    uniqueReferences.push(reference);
  }

  return {
    references: uniqueReferences,
    avatarReferences: uniqueReferences.slice(0, maxVisibleAvatars),
    overflowCount: Math.max(0, uniqueReferences.length - maxVisibleAvatars),
    sectionLabel: 'Sources:',
    countLabel: `${uniqueReferences.length} ${uniqueReferences.length === 1 ? 'email' : 'emails'}`,
  };
}
