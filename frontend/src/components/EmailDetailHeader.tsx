'use client';

import { MessageSquareText } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { EmailDetail } from '@/lib/api';
import { EMAIL_AI_ACTION_LABEL } from '@/lib/workspace-chrome-contract.mjs';

interface AvatarPresentation {
  background: string;
  foreground: string;
  initials: string;
}

interface EmailDetailHeaderProps {
  avatar: AvatarPresentation | null;
  detail: EmailDetail | null;
  isLoading: boolean;
  receivedAt?: string;
  onAskAI: () => void;
}

export function EmailDetailHeader({
  avatar,
  detail,
  isLoading,
  receivedAt,
  onAskAI,
}: EmailDetailHeaderProps) {
  return (
    <div className="shrink-0 border-b border-border bg-muted/30 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <h2 className="min-w-0 flex-1 text-base font-semibold leading-snug text-foreground">
          {detail?.subject || (isLoading ? 'Loading…' : '')}
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          {detail && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-foreground"
              onClick={onAskAI}
              aria-label={EMAIL_AI_ACTION_LABEL}
              title={EMAIL_AI_ACTION_LABEL}
            >
              <MessageSquareText className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      {detail && (
        <div className="mt-1.5 flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <div className="flex min-w-0 items-center gap-2">
            <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${avatar?.background} ${avatar?.foreground}`}>
              {avatar?.initials}
            </div>
            <span className="truncate">
              From: <span className="font-medium text-foreground">{detail.sender}</span>
            </span>
          </div>
          <span className="shrink-0 whitespace-nowrap text-xs">{receivedAt}</span>
        </div>
      )}
    </div>
  );
}
