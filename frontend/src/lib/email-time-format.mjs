export function formatEmailDateTime(timestamp, { locale, timeZone } = {}) {
  if (!Number.isFinite(timestamp)) return '';

  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return '';

  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      ...(timeZone ? { timeZone } : {}),
    }).format(date);
  } catch {
    return '';
  }
}
