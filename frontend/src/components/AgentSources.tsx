'use client';

import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

import type { AgentReference } from '@/lib/api';
import { getAgentSourcesPresentation } from '@/lib/agent-sources-state.mjs';
import { getAvatarPresentation } from '@/lib/email-avatar-presentation.mjs';
import { cn } from '@/lib/utils';

interface AgentSourcesProps {
  references: AgentReference[];
  onOpenEmail: (emailId: string) => void;
}

export function AgentSources({ references, onOpenEmail }: AgentSourcesProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const presentation = getAgentSourcesPresentation(references) as {
    references: AgentReference[];
    avatarReferences: AgentReference[];
    overflowCount: number;
    sectionLabel: string;
    countLabel: string;
  };

  if (presentation.references.length === 0) return null;

  return (
    <div className="mt-2 w-[90%] max-w-md overflow-hidden rounded-lg border border-border/70 bg-card">
      <button
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((expanded) => !expanded)}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-accent/60"
      >
        <span className="shrink-0 text-xs font-medium text-muted-foreground">
          {presentation.sectionLabel}
        </span>
        <span className="flex shrink-0 -space-x-2" aria-hidden="true">
          {presentation.avatarReferences.map((reference) => {
            const avatar = getAvatarPresentation(reference.sender || '?');
            return (
              <span
                key={reference.email_id}
                className={cn(
                  'flex h-6 w-6 items-center justify-center rounded-full border-2 border-card text-[9px] font-semibold',
                  avatar.background,
                  avatar.foreground,
                )}
              >
                {avatar.initials}
              </span>
            );
          })}
          {presentation.overflowCount > 0 && (
            <span className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-card bg-muted text-[9px] font-semibold text-muted-foreground">
              +{presentation.overflowCount}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
          {presentation.countLabel}
        </span>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform',
            isExpanded && 'rotate-180',
          )}
        />
      </button>

      {isExpanded && (
        <div className="max-h-52 overflow-y-auto border-t border-border/70 p-1.5">
          {presentation.references.map((reference) => {
            const sender = reference.sender || 'Unknown sender';
            const avatar = getAvatarPresentation(sender);
            return (
              <button
                key={reference.email_id}
                type="button"
                onClick={() => onOpenEmail(reference.email_id)}
                className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent"
                title={`${sender} · ${reference.subject}`}
              >
                <span
                  className={cn(
                    'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold',
                    avatar.background,
                    avatar.foreground,
                  )}
                >
                  {avatar.initials}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">{sender}</span>
                  <span className="block truncate text-xs text-muted-foreground">{reference.subject}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
