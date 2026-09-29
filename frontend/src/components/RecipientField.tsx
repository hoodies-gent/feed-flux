'use client';

import { useState, type ClipboardEvent, type FocusEvent, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import {
  commitRecipientInput,
  parseRecipientList,
  removeRecipient,
} from '@/lib/recipient-list.mjs';

interface RecipientFieldProps {
  id: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  onCommit: (value: string) => void;
  onPendingChange: (pending: boolean) => void;
}

export function RecipientField({
  id,
  value,
  disabled = false,
  onChange,
  onCommit,
  onPendingChange,
}: RecipientFieldProps) {
  const [input, setInput] = useState('');
  const recipients = parseRecipientList(value) as string[];

  const updateInput = (nextInput: string) => {
    setInput(nextInput);
    onPendingChange(Boolean(nextInput.trim()));
  };

  const commitInput = (rawInput = input) => {
    if (!rawInput.trim()) return;
    const result = commitRecipientInput(value, rawInput);
    if (result.value !== value) {
      onChange(result.value);
      onCommit(result.value);
    }
    const invalidInput = result.invalid.join(', ');
    updateInput(invalidInput);
    if (result.invalid.length > 0) {
      toast.error(result.invalid.length === 1
        ? 'Enter a valid email address.'
        : 'Some email addresses are invalid.');
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if ((event.key === 'Enter' || event.key === ',' || event.key === ';') && input.trim()) {
      event.preventDefault();
      commitInput();
      return;
    }
    if (event.key === 'Backspace' && !input && recipients.length > 0) {
      const nextValue = removeRecipient(value, recipients.at(-1) ?? '');
      onChange(nextValue);
      onCommit(nextValue);
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData('text');
    if (!/[,;\n]/.test(pasted)) return;
    event.preventDefault();
    commitInput(pasted);
  };

  const handleBlur = (event: FocusEvent<HTMLInputElement>) => {
    if (event.relatedTarget instanceof Node
      && event.currentTarget.parentElement?.contains(event.relatedTarget)) {
      return;
    }
    commitInput();
  };

  const handleRemove = (recipient: string) => {
    const nextValue = removeRecipient(value, recipient);
    onChange(nextValue);
    onCommit(nextValue);
  };

  return (
    <div className="flex min-h-7 min-w-0 flex-wrap items-center gap-1 rounded-md px-1.5 py-1 focus-within:ring-1 focus-within:ring-ring">
      {recipients.map((recipient) => (
        <span
          key={recipient.toLowerCase()}
          className="inline-flex max-w-full items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-sm text-foreground"
        >
          <span className="truncate">{recipient}</span>
          <button
            type="button"
            aria-label={`Remove ${recipient}`}
            className="shrink-0 rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onClick={() => handleRemove(recipient)}
            disabled={disabled}
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        id={id}
        type="email"
        multiple
        value={input}
        onChange={(event) => updateInput(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onBlur={handleBlur}
        disabled={disabled}
        aria-label="Add recipient"
        className="h-5 min-w-28 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
      />
    </div>
  );
}
