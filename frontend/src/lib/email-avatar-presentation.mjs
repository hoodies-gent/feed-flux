const avatarPalettes = [
  { background: 'bg-blue-100 dark:bg-blue-900/40', foreground: 'text-blue-700 dark:text-blue-300' },
  { background: 'bg-indigo-100 dark:bg-indigo-900/40', foreground: 'text-indigo-700 dark:text-indigo-300' },
  { background: 'bg-teal-100 dark:bg-teal-900/40', foreground: 'text-teal-700 dark:text-teal-300' },
  { background: 'bg-emerald-100 dark:bg-emerald-900/40', foreground: 'text-emerald-700 dark:text-emerald-300' },
  { background: 'bg-violet-100 dark:bg-violet-900/40', foreground: 'text-violet-700 dark:text-violet-300' },
  { background: 'bg-amber-100 dark:bg-amber-900/40', foreground: 'text-amber-700 dark:text-amber-300' },
];

export function getAvatarPresentation(label) {
  const normalized = label.trim() || '?';
  const words = normalized.split(/\s+/).filter(Boolean);
  const initials = words.length > 1
    ? `${words[0][0]}${words[words.length - 1][0]}`
    : normalized.slice(0, 1);
  const hash = Array.from(normalized).reduce((value, character) => (
    (value * 31 + character.charCodeAt(0)) >>> 0
  ), 0);

  return {
    initials: initials.toUpperCase(),
    ...avatarPalettes[hash % avatarPalettes.length],
  };
}
