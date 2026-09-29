import ReactMarkdown from 'react-markdown';

import type { AgentReference } from '@/lib/api';
import { getAvatarPresentation } from '@/lib/email-avatar-presentation.mjs';
import {
  addMissingInlineEmailCitations,
  linkifyInlineEmailCitations,
  resolveInlineEmailCitation,
} from '@/lib/inline-email-citations.mjs';
import { cn } from '@/lib/utils';

interface AgentMessageMarkdownProps {
  content: string;
  isStreaming: boolean;
  references: AgentReference[];
  onOpenEmail: (emailId: string) => void;
}

function InlineEmailCitation({
  reference,
  onOpenEmail,
}: {
  reference: AgentReference;
  onOpenEmail: (emailId: string) => void;
}) {
  const sender = reference.sender || 'Unknown sender';
  const avatar = getAvatarPresentation(sender);

  return (
    <button
      type="button"
      aria-label={`Open email: ${reference.subject}`}
      onClick={() => onOpenEmail(reference.email_id)}
      style={{ verticalAlign: 'text-bottom' }}
      className={cn(
        'not-prose group relative mx-0.5 inline-flex h-[18px] w-[18px]',
        'items-center justify-center rounded-full outline-none ring-offset-background',
        'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
      )}
    >
      <span
        className={cn(
          'flex h-[18px] w-[18px] items-center justify-center rounded-full text-[7px] font-semibold',
          avatar.background,
          avatar.foreground,
        )}
      >
        {avatar.initials}
      </span>
      <span
        role="tooltip"
        className={cn(
          'pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 z-30',
          'flex h-7 w-max max-w-44 -translate-x-1/2 items-center gap-1.5 rounded-full',
          'border border-border bg-popover px-1.5 pr-2.5 text-popover-foreground shadow-md',
          'origin-bottom scale-95 opacity-0 transition duration-150 ease-out',
          'group-hover:scale-100 group-hover:opacity-100',
          'group-focus-visible:scale-100 group-focus-visible:opacity-100',
        )}
      >
        <span
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[6px] font-semibold',
            avatar.background,
            avatar.foreground,
          )}
        >
          {avatar.initials}
        </span>
        <span className="truncate text-[11px] font-medium">{reference.subject}</span>
      </span>
    </button>
  );
}

export function AgentMessageMarkdown({
  content,
  isStreaming,
  references,
  onOpenEmail,
}: AgentMessageMarkdownProps) {
  const citedContent = addMissingInlineEmailCitations(
    content,
    references,
    isStreaming,
  );

  return (
    <ReactMarkdown
      components={{
        a: ({ href, children }) => {
          const citation = resolveInlineEmailCitation(
            href,
            references,
            isStreaming,
          );
          if (citation.state === 'resolved') {
            return (
              <InlineEmailCitation
                reference={citation.reference as AgentReference}
                onOpenEmail={onOpenEmail}
              />
            );
          }
          if (citation.state === 'pending') {
            return (
              <span
                aria-hidden="true"
                style={{ verticalAlign: 'text-bottom' }}
                className="not-prose mx-0.5 inline-block h-[18px] w-[18px] animate-pulse rounded-full bg-muted"
              />
            );
          }
          if (citation.state === 'hidden') return null;
          return <a href={href}>{children}</a>;
        },
      }}
    >
      {linkifyInlineEmailCitations(citedContent)}
    </ReactMarkdown>
  );
}
