export function getFeedItemStateClassName(isActive) {
  return isActive
    ? 'border-b border-b-border border-l-[3px] border-l-primary bg-foreground/[0.11]'
    : 'border-b border-b-border border-l-[3px] border-l-transparent hover:bg-foreground/[0.06]';
}
