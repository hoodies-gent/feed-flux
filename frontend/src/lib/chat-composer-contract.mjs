export function shouldSubmitChatInput({ key, shiftKey }) {
  return key === 'Enter' && !shiftKey;
}
