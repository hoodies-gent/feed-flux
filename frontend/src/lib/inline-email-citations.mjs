const INLINE_CITATION_PATTERN = /<!--feedflux_ref:([A-Za-z0-9_-]+)-->/g;
const TRAILING_PARTIAL_CITATION_PATTERN = /<!--feedflux_ref:[^<>]*$/;
const CITATION_HREF_PREFIX = '#feedflux-citation=';

export function linkifyInlineEmailCitations(markdown) {
  return markdown
    .replace(
      INLINE_CITATION_PATTERN,
      (_, citationKey) => `[citation](${CITATION_HREF_PREFIX}${citationKey})`,
    )
    .replace(TRAILING_PARTIAL_CITATION_PATTERN, '');
}

export function getInlineCitationKey(href) {
  if (typeof href !== 'string' || !href.startsWith(CITATION_HREF_PREFIX)) {
    return null;
  }
  const citationKey = href.slice(CITATION_HREF_PREFIX.length);
  return /^[A-Za-z0-9_-]+$/.test(citationKey) ? citationKey : null;
}

export function resolveInlineEmailCitation(href, references, isStreaming) {
  const citationKey = getInlineCitationKey(href);
  if (citationKey === null) return { state: 'not-citation' };

  const reference = references.find(
    (candidate) => candidate.citation_key === citationKey,
  );
  if (reference) return { state: 'resolved', reference };
  return { state: isStreaming ? 'pending' : 'hidden' };
}
