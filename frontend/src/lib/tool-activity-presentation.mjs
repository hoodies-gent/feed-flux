const TOOL_ACTIVITY_LABELS = {
  send_test_email: ['Saving test email', 'Saved test email'],
  find_email: ['Finding emails', 'Found emails'],
  read_calendar: ['Checking calendar', 'Checked calendar'],
  save_reply_draft: ['Saving reply draft', 'Saved reply draft'],
  apply_draft_patch: ['Updating draft', 'Updated draft'],
  read_draft_context: ['Reading draft', 'Read draft'],
  read_original_email_context: ['Reading email', 'Read email'],
  apply_triage_batch: ['Preparing inbox plan', 'Prepared inbox plan'],
  remember_memory: ['Saving to memory', 'Saved to memory'],
  list_memories: ['Checking memory', 'Checked memory'],
  update_memory: ['Updating memory', 'Updated memory'],
  forget_memory: ['Removing from memory', 'Removed from memory'],
  reset_memories: ['Clearing memory', 'Cleared memory'],
  record_memory_candidate: ['Reviewing memory suggestion', 'Reviewed memory suggestion'],
};

const INBOX_ACTIVITY_LABELS = {
  overview: ['Reviewing inbox', 'Reviewed inbox'],
  attention: ['Finding what needs attention', 'Found what needs attention'],
  triage: ['Scanning unread', 'Scanned unread'],
};

function resultSummary(tool, running, resultCount) {
  if (running || typeof resultCount !== 'number') return '';
  if (tool === 'find_email' || tool === 'list_inbox_emails') {
    if (resultCount === 0) {
      return tool === 'find_email' ? 'No matching emails' : 'No emails';
    }
    return `${resultCount} ${resultCount === 1 ? 'email' : 'emails'}`;
  }
  if (tool === 'list_memories') {
    return `${resultCount} ${resultCount === 1 ? 'memory' : 'memories'}`;
  }
  return '';
}

export function getToolActivityPresentation({
  tool,
  running,
  args,
  resultCount,
}) {
  const purpose = args && typeof args === 'object' ? args.purpose : undefined;
  const labels = tool === 'list_inbox_emails'
    ? INBOX_ACTIVITY_LABELS[purpose] ?? INBOX_ACTIVITY_LABELS.overview
    : TOOL_ACTIVITY_LABELS[tool];

  return {
    label: labels?.[running ? 0 : 1] ?? (running ? 'Working' : 'Completed'),
    summary: resultSummary(tool, running, resultCount),
  };
}
