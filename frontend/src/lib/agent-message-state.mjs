export function shouldShowMessageReferences(message) {
  const references = message.references ?? message.sources ?? [];
  return message.role === 'assistant'
    && message.isStreaming !== true
    && references.length > 0;
}

export function restoreChatMessageState(messages) {
  return messages.map((message) => (
    message.role === 'assistant'
      ? { ...message, isLoading: false, isStreaming: false }
      : message
  ));
}

export function getInlineCitationReferences(messages, messageIndex) {
  const currentReferences = (messages[messageIndex]?.references ?? []).filter(
    (reference) => reference?.email_id,
  );
  if (currentReferences.length > 0) return currentReferences;

  for (let index = messageIndex - 1; index >= 0; index -= 1) {
    const priorReferences = (messages[index]?.references ?? []).filter(
      (reference) => reference?.email_id,
    );
    if (priorReferences.length === 0) continue;
    return priorReferences;
  }

  return [];
}
