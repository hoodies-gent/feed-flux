const emailPattern = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

function splitRecipients(value) {
  return value
    .split(/[,;\n]+/)
    .map((recipient) => recipient.trim())
    .filter(Boolean);
}

function uniqueRecipients(recipients) {
  const seen = new Set();
  return recipients.filter((recipient) => {
    const key = recipient.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function parseRecipientList(value) {
  return uniqueRecipients(splitRecipients(value));
}

export function commitRecipientInput(currentValue, input) {
  const candidates = splitRecipients(input);
  const valid = candidates.filter((recipient) => emailPattern.test(recipient));
  const invalid = candidates.filter((recipient) => !emailPattern.test(recipient));
  const recipients = uniqueRecipients([...parseRecipientList(currentValue), ...valid]);

  return {
    value: recipients.join(', '),
    recipients,
    invalid,
  };
}

export function removeRecipient(currentValue, recipientToRemove) {
  return parseRecipientList(currentValue)
    .filter((recipient) => recipient.toLowerCase() !== recipientToRemove.toLowerCase())
    .join(', ');
}
