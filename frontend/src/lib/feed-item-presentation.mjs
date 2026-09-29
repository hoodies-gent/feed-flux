export function getFeedItemStateClassName(isActive) {
  return isActive
    ? 'border-b border-b-border border-l-[3px] border-l-primary bg-foreground/[0.11]'
    : 'border-b border-b-border border-l-[3px] border-l-transparent hover:bg-foreground/[0.06]';
}

export function getFeedItemSubjectClassName(isRead) {
  return isRead
    ? 'min-w-0 flex-1 truncate text-sm font-normal text-foreground'
    : 'min-w-0 flex-1 truncate text-sm font-semibold text-blue-700 dark:text-blue-300';
}

export function getFeedItemSenderClassName(isRead) {
  return isRead
    ? 'min-w-0 flex-1 truncate text-sm font-normal text-muted-foreground'
    : 'min-w-0 flex-1 truncate text-sm font-semibold text-foreground';
}

export function getFeedItemTimeClassName(isRead) {
  return isRead
    ? 'shrink-0 text-xs text-muted-foreground transition-opacity group-hover:opacity-0'
    : 'shrink-0 text-xs font-medium text-blue-600 dark:text-blue-400 transition-opacity group-hover:opacity-0';
}
