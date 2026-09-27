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
