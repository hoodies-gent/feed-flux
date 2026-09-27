export const APP_HEADER_SEARCH_PLACEHOLDER = 'Search emails by keyword, or type a question and click Ask AI';
export const APP_HEADER_MORE_LABEL = 'More';
export const APP_HEADER_SEARCH_INPUT_PROPS = {
  name: 'email-search',
  autoComplete: 'off',
  'aria-label': 'Search emails',
};

export function getAskAiPrompt(value) {
  return value.trim();
}
