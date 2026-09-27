'use client';

import type { ReactNode } from 'react';
import { Mail, Send, Sparkles, Trash2, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { AgentReference } from '@/lib/api';
import {
  AGENT_EMPTY_STATE_DESCRIPTION,
  AGENT_EMPTY_STATE_TITLE,
  AGENT_SUGGESTED_PROMPTS,
} from '@/lib/agent-sidebar-contract.mjs';

interface AgentSidebarProps {
  children: ReactNode;
  composer: ReactNode;
  focusedEmailContext: AgentReference | null;
  hasMessages: boolean;
  isSending: boolean;
  messagesEnd: ReactNode;
  onClearFocus: () => void;
  onClose: () => void;
  onNewChat: () => void;
  onOpenFocusedEmail: (emailId: string) => void;
  onSuggestion: (prompt: string) => void;
}

function EmptyAgentState({
  isSending,
  onSuggestion,
}: Pick<AgentSidebarProps, 'isSending' | 'onSuggestion'>) {
  return (
    <div className="flex h-full flex-col items-center justify-center space-y-4 pt-16 text-center">
      <div className="rounded-full bg-muted p-4">
        <Sparkles className="h-8 w-8 text-muted-foreground" />
      </div>
      <div>
        <h3 className="mb-1 text-lg font-semibold text-foreground">{AGENT_EMPTY_STATE_TITLE}</h3>
        <p className="mx-auto max-w-[280px] text-sm text-muted-foreground">
          {AGENT_EMPTY_STATE_DESCRIPTION}
        </p>
      </div>
      <div className="relative mt-2 flex w-fit max-w-full flex-col items-center gap-1 rounded-xl border border-border/70 px-3 py-2.5">
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-card px-2 text-sm text-muted-foreground">
          For example
        </span>
        {AGENT_SUGGESTED_PROMPTS.map((prompt) => (
          <Button
            key={prompt}
            type="button"
            variant="ghost"
            className="group h-auto max-w-full cursor-pointer justify-center whitespace-normal px-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            disabled={isSending}
            onClick={() => onSuggestion(prompt)}
          >
            <span className="flex max-w-full items-center gap-1.5">
              <span className="relative inline-flex h-3 w-3 shrink-0 items-center justify-center" aria-hidden="true">
                <span className="absolute delay-100 transition-all duration-100 ease-in group-hover:delay-0 group-hover:translate-y-1.5 group-hover:opacity-0 group-focus-visible:delay-0 group-focus-visible:translate-y-1.5 group-focus-visible:opacity-0">&gt;</span>
                <Send className="absolute size-3 translate-y-1.5 scale-90 opacity-0 delay-0 transition-all duration-100 ease-out group-hover:delay-100 group-hover:translate-y-0 group-hover:scale-100 group-hover:opacity-100 group-focus-visible:delay-100 group-focus-visible:translate-y-0 group-focus-visible:scale-100 group-focus-visible:opacity-100" />
              </span>
              <span className="min-w-0">{prompt}</span>
            </span>
          </Button>
        ))}
      </div>
    </div>
  );
}

export function AgentSidebar({
  children,
  composer,
  focusedEmailContext,
  hasMessages,
  isSending,
  messagesEnd,
  onClearFocus,
  onClose,
  onNewChat,
  onOpenFocusedEmail,
  onSuggestion,
}: AgentSidebarProps) {
  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-muted/30 p-4">
        <div className="flex items-center gap-2">
          <div className="rounded-md bg-muted p-1.5">
            <Sparkles className="h-4 w-4 text-foreground" />
          </div>
          <h2 className="text-sm font-semibold text-foreground">Inbox QA Assistant</h2>
        </div>
        <div className="flex items-center gap-1">
          {hasMessages && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              onClick={onNewChat}
              title="New chat (clears history and resets thread)"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="-mr-2 h-8 w-8 text-muted-foreground hover:text-foreground"
            onClick={onClose}
            title="Close Agent"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {focusedEmailContext && (
        <div className="flex shrink-0 items-center gap-3 border-b border-border bg-primary/5 px-4 py-3">
          <button
            type="button"
            onClick={() => onOpenFocusedEmail(focusedEmailContext.email_id)}
            className="flex min-w-0 flex-1 items-center gap-2 text-left"
            title={`${focusedEmailContext.sender} · ${focusedEmailContext.subject}`}
          >
            <Mail className="h-4 w-4 shrink-0 text-primary" />
            <span className="min-w-0">
              <span className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Focused email
              </span>
              <span className="block truncate text-xs font-medium text-foreground">
                {focusedEmailContext.subject}
              </span>
              <span className="block truncate text-[11px] text-muted-foreground">
                {focusedEmailContext.sender}
              </span>
            </span>
          </button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={onClearFocus}
            title="Stop treating this email as the conversational focus"
          >
            Clear focus
          </Button>
        </div>
      )}

      <div className="relative flex-1 overflow-y-auto p-5">
        <div className="space-y-6 pb-2">
          {hasMessages ? children : (
            <EmptyAgentState isSending={isSending} onSuggestion={onSuggestion} />
          )}
          {messagesEnd}
        </div>
      </div>

      {composer}
    </aside>
  );
}
