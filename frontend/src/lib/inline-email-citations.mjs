const INLINE_CITATION_PATTERN = /<!--feedflux_ref:([A-Za-z0-9_-]+)-->/g;
const TRAILING_PARTIAL_CITATION_PATTERN = /<!--feedflux_ref:[^<>]*$/;
const CITATION_HREF_PREFIX = '#feedflux-citation=';
const MIN_SUBJECT_MATCH_LENGTH = 8;

function normalizedMention(value) {
  return typeof value === 'string' ? value.trim().toLocaleLowerCase() : '';
}

function uniqueMentionCounts(references, field) {
  const counts = new Map();
  for (const reference of references) {
    const mention = normalizedMention(reference[field]);
    if (mention) counts.set(mention, (counts.get(mention) ?? 0) + 1);
  }
  return counts;
}

function citationBoundary(markdown, start) {
  const pairedClosers = ['**', '__', '~~'];
  const singleClosers = new Set([
    '`', '"', "'", '”', '’', '」', '』', '》', ')', '）', ']', '】',
    '.', ',', '!', '?', ':', ';', '。', '，', '！', '？', '：', '；',
  ]);
  let end = start;
  let advanced = true;

  while (advanced) {
    advanced = false;
    for (const closer of pairedClosers) {
      if (markdown.startsWith(closer, end)) {
        end += closer.length;
        advanced = true;
        break;
      }
    }
    if (!advanced && singleClosers.has(markdown[end])) {
      end += 1;
      advanced = true;
    }
  }
  return end;
}

function addCitationAfterFirstMention(markdown, mention, citationKey) {
  const index = markdown.indexOf(mention);
  if (index < 0) return markdown;
  const boundary = citationBoundary(markdown, index + mention.length);
  const marker = `<!--feedflux_ref:${citationKey}-->`;
  return `${markdown.slice(0, boundary)}${marker}${markdown.slice(boundary)}`;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function normalizeKnownCitationTokens(markdown, references) {
  const citationKeys = new Set(
    references
      .map((reference) => reference.citation_key)
      .filter((citationKey) => /^[A-Za-z0-9_-]+$/.test(citationKey)),
  );
  let result = markdown.replace(
    /[（(]([^()（）]+)[）)]/g,
    (group, content) => {
      const groupKeys = content.split(/[、,，]/).map((value) => value.trim());
      if (
        groupKeys.length === 0
        || groupKeys.some((citationKey) => !citationKeys.has(citationKey))
      ) {
        return group;
      }
      return groupKeys
        .map((citationKey) => `<!--feedflux_ref:${citationKey}-->`)
        .join('');
    },
  );

  for (const citationKey of citationKeys) {
    const marker = `<!--feedflux_ref:${citationKey}-->`;
    const tokenPattern = new RegExp(escapeRegExp(marker), 'g');
    let keptCitation = false;
    result = result.replace(tokenPattern, () => {
      if (keptCitation) return '';
      keptCitation = true;
      return marker;
    });
  }

  return result;
}

export function addMissingInlineEmailCitations(markdown, references, isStreaming) {
  if (isStreaming || !Array.isArray(references) || references.length === 0) {
    return markdown;
  }

  const eligible = references.filter(
    (reference) => reference?.citation_key && reference?.email_id,
  );
  const subjectCounts = uniqueMentionCounts(eligible, 'subject');
  const senderCounts = uniqueMentionCounts(eligible, 'sender');
  let result = normalizeKnownCitationTokens(markdown, eligible);

  for (const reference of eligible) {
    const marker = `<!--feedflux_ref:${reference.citation_key}-->`;
    if (result.includes(marker)) continue;

    const subject = typeof reference.subject === 'string'
      ? reference.subject.trim()
      : '';
    const normalizedSubject = normalizedMention(subject);
    if (
      subject.length >= MIN_SUBJECT_MATCH_LENGTH
      && subjectCounts.get(normalizedSubject) === 1
      && result.includes(subject)
    ) {
      result = addCitationAfterFirstMention(result, subject, reference.citation_key);
      continue;
    }

    const sender = typeof reference.sender === 'string'
      ? reference.sender.trim()
      : '';
    if (
      sender
      && senderCounts.get(normalizedMention(sender)) === 1
      && result.includes(sender)
    ) {
      result = addCitationAfterFirstMention(result, sender, reference.citation_key);
    }
  }

  return result;
}

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
