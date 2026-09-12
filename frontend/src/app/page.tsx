'use client';

import { useEffect, useState, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getFeed, summarizeEmail, getEmailDetail, syncEmails, askAgentStream, resumeAgent, getDailyBriefing, getConfigStatus, setupConfig, mockLogin, triageAction, triageUndo, type FeedItem, type SummaryResponse, type EmailDetail, type SourceItem, type BriefingResponse, type TraceEvent, type InterruptEvent, type InterruptReference, type AgentStreamCallbacks, type BulkTriageItem, type NeedsReplyItem, type TriagePlan, type TriageActionKind, type DraftReply } from '@/lib/api';
import { DraftWorkspace } from '@/components/DraftWorkspace';
import { Textarea } from "@/components/ui/textarea";
import { toast } from 'sonner';
import { useDebounce } from 'use-debounce';
import { Input } from "@/components/ui/input";
import { Trash2, Send, RefreshCw, X, Sparkles, Search, Copy, Check, CheckCircle2, ChevronDown, ChevronUp, ChevronRight, User, Wrench, Hand, Mail, BookOpen, Archive, MessageSquare, Loader2, Pencil } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { type PanelImperativeHandle } from "react-resizable-panels";
import { cn } from "@/lib/utils";

type MessageSegment =
  | { kind: 'text'; text: string }
  | { kind: 'tool_start'; tool: string; args?: unknown }
  | { kind: 'tool_end'; tool: string; output?: string; resultCount?: number };

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: SourceItem[];
  isLoading?: boolean;
  segments?: MessageSegment[];
  pendingInterrupt?: InterruptEvent;
  triagePlan?: TriagePlan;
}

function argsPreview(args: unknown): string {
  if (args === undefined || args === null) return '';
  if (typeof args !== 'object') return String(args);
  const entries = Object.entries(args as Record<string, unknown>);
  if (entries.length === 0) return '';
  return entries
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ');
}

function outputSummary(tool: string, output: string | undefined, resultCount?: number): string {
  if (tool === 'find_email' || tool === 'list_unread_emails') {
    if (typeof resultCount === 'number') {
      return resultCount === 0 ? 'no matches' : `${resultCount} email${resultCount === 1 ? '' : 's'}`;
    }
    if (!output) return '';
    if (output.startsWith('[]')) return 'no matches';
    const count = (output.match(/'id':/g) || []).length;
    return count > 0 ? `${count}+ emails` : 'ok';
  }
  if (!output) return '';
  if (tool === 'read_calendar') {
    const slots = (output.match(/'[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}'/g) || []).length;
    return slots > 0 ? `${slots} free slots` : 'ok';
  }
  if (tool === 'send_reply') {
    return output.startsWith('SEND COMPLETE') ? 'sent (dry-run)' : 'ok';
  }
  if (tool === 'apply_triage_batch') {
    const m = output.match(/PLAN READY: (\d+) bulk items \+ (\d+) needs-reply/);
    if (m) return `${m[1]} bulk · ${m[2]} needs reply`;
    return 'plan ready';
  }
  return output.length > 40 ? output.slice(0, 40).replace(/\s+/g, ' ') + '…' : output;
}

const avatarPalettes = [
  { background: 'bg-blue-100 dark:bg-blue-900/40', foreground: 'text-blue-700 dark:text-blue-300' },
  { background: 'bg-indigo-100 dark:bg-indigo-900/40', foreground: 'text-indigo-700 dark:text-indigo-300' },
  { background: 'bg-teal-100 dark:bg-teal-900/40', foreground: 'text-teal-700 dark:text-teal-300' },
  { background: 'bg-emerald-100 dark:bg-emerald-900/40', foreground: 'text-emerald-700 dark:text-emerald-300' },
  { background: 'bg-violet-100 dark:bg-violet-900/40', foreground: 'text-violet-700 dark:text-violet-300' },
  { background: 'bg-amber-100 dark:bg-amber-900/40', foreground: 'text-amber-700 dark:text-amber-300' },
];

function getAvatarPresentation(label: string) {
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

function ToolCallLine({
  tool,
  args,
  output,
  resultCount,
  running,
}: {
  tool: string;
  args?: unknown;
  output?: string;
  resultCount?: number;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const preview = argsPreview(args);
  return (
    <div className="my-1.5 pl-2 border-l-2 border-border/60 font-mono text-[11px] text-muted-foreground">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 hover:text-foreground transition-colors text-left"
      >
        {running ? (
          <Wrench className="w-3 h-3 shrink-0 animate-pulse" />
        ) : (
          <Check className="w-3 h-3 shrink-0 text-emerald-600 dark:text-emerald-500" />
        )}
        <span className="flex-1 truncate">
          <span className="text-foreground">{tool}</span>
          {preview && <span>({preview})</span>}
          {!running && output !== undefined && (
            <span className="text-muted-foreground/70"> · {outputSummary(tool, output, resultCount)}</span>
          )}
        </span>
        <ChevronRight className={`w-3 h-3 shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="pl-5 pt-1 pb-0.5 space-y-1 text-[10px] whitespace-pre-wrap break-all">
          {args !== undefined && args !== null && (
            <div>
              <span className="text-muted-foreground">args: </span>
              <span>{JSON.stringify(args, null, 2)}</span>
            </div>
          )}
          {output !== undefined && (
            <div>
              <span className="text-muted-foreground">→ </span>
              <span>{output}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type RenderItem =
  | { kind: 'text'; text: string; key: number }
  | { kind: 'tool'; tool: string; args?: unknown; output?: string; resultCount?: number; running: boolean; key: number };

function pairSegments(segments: MessageSegment[]): RenderItem[] {
  const items: RenderItem[] = [];
  const usedEnds = new Set<number>();
  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    if (s.kind === 'text') {
      items.push({ kind: 'text', text: s.text, key: i });
    } else if (s.kind === 'tool_start') {
      let matchIdx = -1;
      for (let j = i + 1; j < segments.length; j++) {
        const s2 = segments[j];
        if (s2.kind === 'tool_end' && s2.tool === s.tool && !usedEnds.has(j)) {
          matchIdx = j;
          break;
        }
      }
      if (matchIdx >= 0) {
        usedEnds.add(matchIdx);
        const end = segments[matchIdx] as Extract<MessageSegment, { kind: 'tool_end' }>;
        items.push({ kind: 'tool', tool: s.tool, args: s.args, output: end.output, resultCount: end.resultCount, running: false, key: i });
      } else {
        items.push({ kind: 'tool', tool: s.tool, args: s.args, running: true, key: i });
      }
    }
  }
  return items;
}

function InterruptApprovalCard({
  interrupt,
  disabled,
  onDecide,
}: {
  interrupt: InterruptEvent;
  disabled: boolean;
  onDecide: (approve: boolean, note?: string) => void;
}) {
  const [note, setNote] = useState('');
  return (
    <div className="w-[90%] mt-1 rounded-xl border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Hand className="w-4 h-4" />
        <span className="text-xs font-medium">Agent is waiting for your confirmation</span>
      </div>
      <div className="text-xs text-foreground">
        About to run <code className="px-1 py-0.5 rounded bg-muted text-[11px]">{interrupt.tool}</code> with:
      </div>
      <pre className="text-[11px] bg-muted rounded p-2 overflow-x-auto max-w-full whitespace-pre-wrap break-all">
        {JSON.stringify(interrupt.args, null, 2)}
      </pre>
      <Input
        value={note}
        onChange={e => setNote(e.target.value)}
        placeholder="Optional note to the agent (e.g. why you're declining)"
        className="h-8 text-xs bg-background"
        disabled={disabled}
      />
      <div className="flex gap-2 justify-end">
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => onDecide(false, note.trim() || undefined)}>
          Decline
        </Button>
        <Button size="sm" disabled={disabled} onClick={() => onDecide(true, note.trim() || undefined)}>
          Confirm
        </Button>
      </div>
    </div>
  );
}

function ReferencesPanel({ refs }: { refs: InterruptReference[] }) {
  if (!refs || refs.length === 0) return null;
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 px-2.5 py-1.5 space-y-1">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium">
        Based on
      </div>
      <ul className="space-y-1 font-mono text-[11px] text-muted-foreground">
        {refs.map((r, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <Wrench className="w-3 h-3 mt-0.5 shrink-0" />
            <span className="break-all line-clamp-2">
              <span className="text-foreground">{r.tool}</span> → {r.output}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MeetingReplyReviewCard({
  interrupt,
  disabled,
  onDecide,
}: {
  interrupt: InterruptEvent;
  disabled: boolean;
  onDecide: (approve: boolean, note?: string, editedBody?: string) => void;
}) {
  const draft = interrupt.draft_preview ?? {};
  const originalBody = draft.body ?? '';
  const [body, setBody] = useState(originalBody);
  const [note, setNote] = useState('');
  const edited = body !== originalBody;

  const editedBody = edited ? body : undefined;

  return (
    <div className="w-[90%] mt-1 rounded-xl border border-border bg-card p-3 space-y-3">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Hand className="w-4 h-4" />
        <span className="text-xs font-medium">Review reply before sending</span>
      </div>

      <ReferencesPanel refs={interrupt.references ?? []} />

      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Mail className="w-3 h-3" />
          <span>To: <span className="text-foreground">{draft.recipient}</span></span>
        </div>
        <div className="text-[11px] text-muted-foreground">
          Subject: <span className="text-foreground">{draft.subject}</span>
        </div>
      </div>

      <Textarea
        value={body}
        onChange={e => setBody(e.target.value)}
        rows={8}
        disabled={disabled}
        className="text-xs bg-background font-mono resize-y min-h-[140px]"
      />

      <Input
        value={note}
        onChange={e => setNote(e.target.value)}
        placeholder="Optional note (e.g. 'make it warmer' — used on Decline)"
        className="h-8 text-xs bg-background"
        disabled={disabled}
      />

      <div className="flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground italic">
          {edited ? 'Draft edited' : 'Sends locally in dry-run mode'}
        </span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={disabled}
            onClick={() => onDecide(false, note.trim() || undefined, editedBody)}>
            Decline
          </Button>
          <Button size="sm" disabled={disabled}
            onClick={() => onDecide(true, note.trim() || undefined, editedBody)}>
            Confirm & Send
          </Button>
        </div>
      </div>
    </div>
  );
}

const BULK_KIND_META: Record<TriageActionKind, { label: string; short: string; pastTense: string; icon: typeof BookOpen; color: string; badge: string }> = {
  mark_read: {
    label: 'Suggested: mark as read',
    short: 'Read',
    pastTense: 'Marked as read',
    icon: BookOpen,
    color: 'text-sky-600 dark:text-sky-400',
    badge: 'bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-500/30',
  },
  archive: {
    label: 'Suggested: archive',
    short: 'Archive',
    pastTense: 'Archived',
    icon: Archive,
    color: 'text-purple-600 dark:text-purple-400',
    badge: 'bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/30',
  },
  delete: {
    label: 'Suggested: delete',
    short: 'Delete',
    pastTense: 'Deleted',
    icon: Trash2,
    color: 'text-red-600 dark:text-red-400',
    badge: 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30',
  },
};

type ItemStatus = 'pending' | 'processing' | 'done' | 'error';
type BulkItemState = { status: ItemStatus; appliedKind?: TriageActionKind; rowId?: number };

function ActionPill({
  kind,
  suggested,
  disabled,
  onClick,
}: {
  kind: TriageActionKind;
  suggested: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const { short, icon: Icon, badge } = BULK_KIND_META[kind];
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={suggested ? `Suggested: ${short}` : `Change to: ${short}`}
      className={cn(
        'flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border transition-colors',
        suggested
          ? badge
          : 'bg-transparent text-muted-foreground/60 border-transparent hover:border-border hover:text-foreground',
        disabled && 'opacity-40 cursor-not-allowed',
      )}
    >
      <Icon className="w-3 h-3" />
      <span>{short}</span>
    </button>
  );
}

function BulkSection({
  kind,
  items,
  itemStates,
  disabled,
  onApply,
  onView,
  onApplyAllSuggested,
}: {
  kind: TriageActionKind;
  items: BulkTriageItem[];
  itemStates: Record<string, BulkItemState>;
  disabled: boolean;
  onApply: (item: BulkTriageItem, chosenKind: TriageActionKind) => void;
  onView: (emailId: string) => void;
  onApplyAllSuggested: (kind: TriageActionKind) => void;
}) {
  if (items.length === 0) return null;
  const { label, icon: Icon, color } = BULK_KIND_META[kind];
  const pending = items.filter(i => (itemStates[i.email_id]?.status ?? 'pending') === 'pending');

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className={cn('flex items-center gap-1.5 text-xs font-medium', color)}>
          <Icon className="w-3.5 h-3.5" />
          <span>{label}</span>
          <span className="text-muted-foreground">({pending.length}/{items.length})</span>
        </div>
        {pending.length > 0 && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onApplyAllSuggested(kind)}
            className="text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
          >
            Apply all suggested
          </button>
        )}
      </div>
      <ul className="space-y-1">
        {items.map(item => {
          const state = itemStates[item.email_id] ?? { status: 'pending' as ItemStatus };
          const isDone = state.status === 'done';
          const isProcessing = state.status === 'processing';
          const isError = state.status === 'error';
          return (
            <li
              key={item.email_id}
              className={cn(
                'group flex items-start gap-2 rounded-md px-2 py-1.5 text-xs transition-all',
                isDone ? 'opacity-40' : 'bg-muted/30 hover:bg-muted/60',
                isError && 'ring-1 ring-red-500/40',
              )}
            >
              <button
                type="button"
                disabled={disabled || isDone}
                onClick={() => onView(item.email_id)}
                title="View original"
                className={cn(
                  'min-w-0 flex-1 flex items-start gap-1.5 text-left rounded transition-colors',
                  !isDone && 'cursor-pointer',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className={cn('block truncate text-foreground', isDone && 'line-through')}>
                    {item.subject ?? item.email_id}
                  </span>
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {item.sender ?? item.sender_email ?? ''}
                    {item.reason && <span> · <span className="italic">{item.reason}</span></span>}
                  </span>
                  {item.body_preview && (
                    <span className="block truncate text-[10px] text-muted-foreground/70 mt-0.5">
                      {item.body_preview}
                    </span>
                  )}
                </span>
              </button>
              <div className="shrink-0 flex items-center gap-1" onClick={e => e.stopPropagation()}>
                {isDone && state.appliedKind && (
                  <span className={cn('text-[10px] px-1.5 py-0.5 rounded border', BULK_KIND_META[state.appliedKind].badge)}>
                    ✓ {BULK_KIND_META[state.appliedKind].pastTense}
                  </span>
                )}
                {isProcessing && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />}
                {!isDone && !isProcessing && (
                  <>
                    {(['mark_read', 'archive', 'delete'] as TriageActionKind[]).map(k => (
                      <ActionPill
                        key={k}
                        kind={k}
                        suggested={item.action === k}
                        disabled={disabled}
                        onClick={() => onApply(item, k)}
                      />
                    ))}
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function NeedsReplySection({
  items,
  disabled,
  onView,
  onDraft,
}: {
  items: NeedsReplyItem[];
  disabled: boolean;
  onView: (emailId: string) => void;
  onDraft: (item: NeedsReplyItem) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
        <MessageSquare className="w-3.5 h-3.5" />
        <span>Needs your reply</span>
        <span className="text-muted-foreground">({items.length})</span>
        <span className="text-[10px] text-muted-foreground font-normal ml-1">— one at a time</span>
      </div>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="group flex items-start gap-2 rounded-md px-2 py-1.5 text-xs bg-amber-500/5 border-l-2 border-l-amber-500/60 hover:bg-amber-500/10 transition-colors">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onView(item.email_id)}
              title="View original"
              className="min-w-0 flex-1 flex items-start gap-1.5 text-left cursor-pointer"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-foreground">{item.subject ?? item.email_id}</span>
                <span className="block truncate text-[10px] text-muted-foreground">
                  {item.sender ?? item.sender_email ?? ''}
                  {item.reason && <span> · <span className="italic">{item.reason}</span></span>}
                </span>
                {item.body_preview && (
                  <span className="block truncate text-[10px] text-muted-foreground/70 mt-0.5">
                    {item.body_preview}
                  </span>
                )}
              </span>
            </button>
            <div className="shrink-0 flex items-center gap-1" onClick={e => e.stopPropagation()}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onDraft(item)}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30 hover:bg-amber-500/20 transition-colors disabled:opacity-40"
                title="Draft a reply"
              >
                <MessageSquare className="w-3 h-3" />
                <span>Draft reply</span>
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BatchTriageReviewCard({
  plan,
  threadId,
  onView,
  onDraft,
}: {
  plan: TriagePlan;
  threadId: string;
  onView: (emailId: string) => void;
  onDraft: (item: NeedsReplyItem) => void;
}) {
  const bulk = plan.bulk ?? [];
  const needsReply = plan.needs_reply ?? [];
  const [itemStates, setItemStates] = useState<Record<string, BulkItemState>>({});

  const applyOne = async (item: BulkTriageItem, chosenKind: TriageActionKind) => {
    setItemStates(prev => ({ ...prev, [item.email_id]: { status: 'processing' } }));
    try {
      const { row_id } = await triageAction(item.email_id, chosenKind, threadId);
      setItemStates(prev => ({ ...prev, [item.email_id]: { status: 'done', appliedKind: chosenKind, rowId: row_id } }));
      const past = BULK_KIND_META[chosenKind].pastTense;
      const title = item.subject ?? item.email_id;
      toast.success(`${past} · ${title.length > 30 ? title.slice(0, 30) + '…' : title}`, {
        duration: 6000,
        action: {
          label: 'Undo',
          onClick: async () => {
            try {
              await triageUndo(row_id);
              setItemStates(prev => {
                const next = { ...prev };
                delete next[item.email_id];
                return next;
              });
              toast.success('Undone');
            } catch (err) {
              toast.error('Undo failed');
            }
          },
        },
      });
    } catch (err) {
      toast.error(`Failed: ${item.subject ?? item.email_id}`);
      setItemStates(prev => ({ ...prev, [item.email_id]: { status: 'error' } }));
    }
  };

  const applyAllSuggestedInSection = async (kind: TriageActionKind) => {
    const targets = bulk.filter(b => b.action === kind && (itemStates[b.email_id]?.status ?? 'pending') === 'pending');
    for (const item of targets) {
      await applyOne(item, kind);
    }
  };

  const markRead = bulk.filter(b => b.action === 'mark_read');
  const archive = bulk.filter(b => b.action === 'archive');
  const del = bulk.filter(b => b.action === 'delete');

  const total = bulk.length + needsReply.length;
  const doneCount = Object.values(itemStates).filter(s => s.status === 'done').length;

  return (
    <div className="w-[95%] mt-1 rounded-xl border border-border bg-card p-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Hand className="w-4 h-4" />
          <span className="text-xs font-medium">
            Batch triage · {total} email{total === 1 ? '' : 's'}
          </span>
        </div>
        <span className="text-[10px] text-muted-foreground">
          {doneCount}/{bulk.length} processed
        </span>
      </div>

      <div className="space-y-3">
        <BulkSection
          kind="mark_read"
          items={markRead}
          itemStates={itemStates}
          disabled={false}
          onApply={applyOne}
          onView={onView}
          onApplyAllSuggested={applyAllSuggestedInSection}
        />
        <BulkSection
          kind="archive"
          items={archive}
          itemStates={itemStates}
          disabled={false}
          onApply={applyOne}
          onView={onView}
          onApplyAllSuggested={applyAllSuggestedInSection}
        />
        <BulkSection
          kind="delete"
          items={del}
          itemStates={itemStates}
          disabled={false}
          onApply={applyOne}
          onView={onView}
          onApplyAllSuggested={applyAllSuggestedInSection}
        />
        <NeedsReplySection
          items={needsReply}
          disabled={false}
          onView={onView}
          onDraft={onDraft}
        />
      </div>

      <div className="text-[10px] text-muted-foreground italic pt-1">
        Dry-run mode · click any action to apply immediately
      </div>
    </div>
  );
}

function SentDryRunChip() {
  return (
    <div className="w-fit flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-[11px] font-medium">
      <CheckCircle2 className="w-3.5 h-3.5" />
      <span>Sent (dry-run mode)</span>
    </div>
  );
}

export default function Home() {
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery] = useDebounce(searchQuery, 500);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, SummaryResponse>>({});
  const [summarizing, setSummarizing] = useState<Record<string, boolean>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);

  // Chat/RAG UI State
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isChatLoaded, setIsChatLoaded] = useState(false);
  // Daily Briefing State
  const [briefing, setBriefing] = useState<string | null>(null);
  const [isBriefingLoading, setIsBriefingLoading] = useState(true);
  const [briefingError, setBriefingError] = useState<string | null>(null);
  const [isBriefingCollapsed, setIsBriefingCollapsed] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [threadId, setThreadId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Load chat history + thread id from LocalStorage strictly on client-side mount
  useEffect(() => {
    const saved = localStorage.getItem('feedflux_chat_history');
    if (saved) {
      try {
        setChatMessages(JSON.parse(saved));
      } catch (e) {
        console.error('Failed to parse persistent chat history', e);
      }
    }
    const savedThread = localStorage.getItem('feedflux_thread_id');
    setThreadId(savedThread || crypto.randomUUID());
    setIsChatLoaded(true);
  }, []);

  // Save chat history + thread id to LocalStorage whenever they change
  useEffect(() => {
    if (isChatLoaded) {
      localStorage.setItem('feedflux_chat_history', JSON.stringify(chatMessages));
      localStorage.setItem('feedflux_thread_id', threadId);
    }
  }, [chatMessages, threadId, isChatLoaded]);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (isChatOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, isChatOpen]);

  // Email Detail Modal State
  const [emailDetailsById, setEmailDetailsById] = useState<Record<string, EmailDetail>>({});
  const [mountedEmailIds, setMountedEmailIds] = useState<string[]>([]);
  const [draftsByEmailId, setDraftsByEmailId] = useState<Record<string, DraftReply[]>>({});
  const [focusedDraftByEmailId, setFocusedDraftByEmailId] = useState<Record<string, number | null>>({});
  const [activeEmailId, setActiveEmailId] = useState<string | null>(null);
  const [loadingEmailIds, setLoadingEmailIds] = useState<Record<string, boolean>>({});

  const [autoDraftOnOpen, setAutoDraftOnOpen] = useState(false);
  const emailDetailData = activeEmailId ? emailDetailsById[activeEmailId] ?? null : null;
  const isLoadingDetail = activeEmailId ? Boolean(loadingEmailIds[activeEmailId]) : false;
  const draftTabs = mountedEmailIds.flatMap((emailId) => (
    (draftsByEmailId[emailId] ?? []).map((draft) => ({
      emailId,
      draft,
      subject: emailDetailsById[emailId]?.subject || 'Reply draft',
    }))
  ));
  const detailAvatar = emailDetailData
    ? getAvatarPresentation(emailDetailData.sender || emailDetailData.sender_email)
    : null;

  const buildStreamCallbacks = (targetMsgId: string): AgentStreamCallbacks => {
    return {
      onTrace: (t) => {
        setChatMessages(prev => prev.map(msg => {
          if (msg.id !== targetMsgId) return msg;
          const segments = [...(msg.segments ?? [])];
          if (t.step === 'tool_start' && t.tool) {
            segments.push({ kind: 'tool_start', tool: t.tool, args: t.args });
          } else if (t.step === 'tool_end' && t.tool) {
            segments.push({ kind: 'tool_end', tool: t.tool, output: t.output, resultCount: t.result_count });
          }
          return { ...msg, segments };
        }));
      },
      onToken: (text) => {
        setChatMessages(prev => prev.map(msg => {
          if (msg.id !== targetMsgId) return msg;
          const segments = [...(msg.segments ?? [])];
          const last = segments[segments.length - 1];
          if (last?.kind === 'text') {
            segments[segments.length - 1] = { kind: 'text', text: last.text + text };
          } else {
            segments.push({ kind: 'text', text });
          }
          return { ...msg, content: (msg.content ?? '') + text, segments, isLoading: false };
        }));
      },
      onInterrupt: (i) => {
        setChatMessages(prev => prev.map(msg =>
          msg.id === targetMsgId ? { ...msg, pendingInterrupt: i, isLoading: false } : msg
        ));
      },
      onPlan: (plan) => {
        setChatMessages(prev => prev.map(msg =>
          msg.id === targetMsgId ? { ...msg, triagePlan: plan, isLoading: false } : msg
        ));
      },
      onDraft: (event) => {
        void handleOpenEmailDetail(event.email_id);
      },
      onDone: () => {},
      onError: (msg) => {
        toast.error(msg);
        setChatMessages(prev => prev.map(m => {
          if (m.id !== targetMsgId) return m;
          const errText = `\n\n_Error: ${msg}_`;
          const segments = [...(m.segments ?? []), { kind: 'text' as const, text: errText }];
          return { ...m, content: (m.content || '') + errText, segments, isLoading: false };
        }));
      },
    };
  };

  const handleSendChatMessage = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!chatInput.trim() || isSendingChat) return;

    const query = chatInput.trim();
    setChatInput('');
    setIsSendingChat(true);

    const userMsgId = crypto.randomUUID();
    const aiMsgId = crypto.randomUUID();
    setChatMessages(prev => [
      ...prev,
      { id: userMsgId, role: 'user', content: query },
      { id: aiMsgId, role: 'assistant', content: '', isLoading: true, segments: [] },
    ]);

    try {
      await askAgentStream(threadId, query, buildStreamCallbacks(aiMsgId));
    } catch (err) {
      toast.error('Failed to reach agent');
      setChatMessages(prev => prev.map(msg =>
        msg.id === aiMsgId
          ? { ...msg, content: 'Sorry, I could not reach the agent.', isLoading: false }
          : msg
      ));
    } finally {
      setIsSendingChat(false);
    }
  };

  const handleResume = async (msgId: string, approve: boolean, note?: string, editedBody?: string) => {
    if (isSendingChat) return;
    setIsSendingChat(true);
    setChatMessages(prev => prev.map(msg =>
      msg.id === msgId ? { ...msg, pendingInterrupt: undefined, isLoading: true } : msg
    ));
    try {
      await resumeAgent(threadId, approve, note, buildStreamCallbacks(msgId), editedBody);
    } catch (err) {
      toast.error('Failed to resume agent');
    } finally {
      setIsSendingChat(false);
    }
  };

  const handleDraftFromTriage = (item: NeedsReplyItem) => {
    void handleOpenEmailDetail(item.email_id, true);
  };

  const handleDraftsChange = (emailId: string, drafts: DraftReply[]) => {
    setDraftsByEmailId((current) => {
      if (drafts.length > 0) return { ...current, [emailId]: drafts };
      if (!(emailId in current)) return current;
      const next = { ...current };
      delete next[emailId];
      return next;
    });
    if (drafts.length === 0 && emailId !== activeEmailId) {
      setMountedEmailIds((current) => current.filter((id) => id !== emailId));
    }
  };

  const handleDraftFocus = (emailId: string, draftId: number | null) => {
    setFocusedDraftByEmailId((current) => ({ ...current, [emailId]: draftId }));
  };

  const handleDraftTabSelect = (emailId: string, draftId: number) => {
    handleDraftFocus(emailId, draftId);
    void handleOpenEmailDetail(emailId);
  };

  const handleNewChat = () => {
    setChatMessages([]);
    setThreadId(crypto.randomUUID());
  };

  const handleOpenEmailDetail = async (id: string, autoDraft = false) => {
    if (activeEmailId && activeEmailId !== id && !(draftsByEmailId[activeEmailId]?.length)) {
      setMountedEmailIds((current) => current.filter((emailId) => emailId !== activeEmailId));
    }
    setAutoDraftOnOpen(autoDraft);
    setActiveEmailId(id);
    setMountedEmailIds((current) => current.includes(id) ? current : [...current, id]);
    if (emailDetailsById[id]) {
      setLoadingEmailIds((current) => ({ ...current, [id]: false }));
      return;
    }
    setLoadingEmailIds((current) => ({ ...current, [id]: true }));
    try {
      const data = await getEmailDetail(id);
      setEmailDetailsById((current) => ({ ...current, [id]: data }));
    } catch (err) {
      toast.error('Failed to load full email content');
    } finally {
      setLoadingEmailIds((current) => ({ ...current, [id]: false }));
    }
  };

  const handleCloseEmailDetail = () => {
    if (activeEmailId && !(draftsByEmailId[activeEmailId]?.length)) {
      setMountedEmailIds((current) => current.filter((emailId) => emailId !== activeEmailId));
    }
    setAutoDraftOnOpen(false);
    setActiveEmailId(null);
  };

  const loadFeed = async (query: string = '', silent: boolean = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFeed(20, query);
      setFeed(data);
      if (!query && !silent) {
        toast.success(`Loaded ${data.length} emails`);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load feed';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setIsSyncing(true);
    try {
      const result = await syncEmails();
      toast.success(`Successfully synced ${result.synced} new emails from Outlook`);
      // Reload feed silently to show new emails
      await loadFeed(debouncedQuery, true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to sync emails';
      toast.error(message);
    } finally {
      setIsSyncing(false);
    }
  };

  const [appState, setAppState] = useState<'checking' | 'setup_keys' | 'mock_login' | 'feed'>('checking');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [isSettingUp, setIsSettingUp] = useState(false);
  const [isMockLoggingIn, setIsMockLoggingIn] = useState(false);

  useEffect(() => {
    const initCheck = async () => {
        try {
            const hasMockLogged = localStorage.getItem('feedflux_mock_logged_in');
            const configRes = await getConfigStatus();
            if (!configRes.configured) {
                setAppState('setup_keys');
            } else if (!hasMockLogged) {
                setAppState('mock_login');
            } else {
                setAppState('feed');
            }
        } catch(e) {
             setAppState('feed');
        }
    };
    initCheck();
  }, []);

  useEffect(() => {
    if (appState === 'feed') {
      loadFeed(debouncedQuery);
    }
  }, [debouncedQuery, appState]);

  const handleSetupKeys = async () => {
      if (!apiKeyInput.trim()) return;
      setIsSettingUp(true);
      try {
          await setupConfig(apiKeyInput.trim());
          toast.success("API Key saved securely.");
          setAppState('mock_login');
      } catch(e) {
          toast.error("Failed to save API key.");
      } finally {
          setIsSettingUp(false);
      }
  };

  const handleMockLogin = async () => {
      setIsMockLoggingIn(true);
      try {
          await mockLogin();
          localStorage.setItem('feedflux_mock_logged_in', 'true');
          toast.success("Successfully authenticated with Outlook!");
          setAppState('feed');
      } catch(e) {
          toast.error("Login failed.");
      } finally {
          setIsMockLoggingIn(false);
      }
  };

  // Load briefing on mount if in feed
  useEffect(() => {
    const fetchBriefing = async () => {
      try {
        setIsBriefingLoading(true);
        setBriefingError(null);
        const res = await getDailyBriefing();
        if (res.error) {
          setBriefingError(res.error);
        } else {
          setBriefing(res.briefing);
        }
      } catch (e) {
        console.error("Failed to load briefing", e);
        setBriefingError("Failed to connect to the intelligence server.");
      } finally {
        setIsBriefingLoading(false);
      }
    };
    fetchBriefing();
  }, []);

  /**
   * Format timestamps like a mail client: relative day labels for recent mail,
   * calendar dates for older messages.
   */
  const formatTime = (timestamp: number) => {
    try {
      const date = new Date(timestamp * 1000);
      const now = new Date();
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const yesterday = new Date(today);
      yesterday.setDate(today.getDate() - 1);
      const time = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
      if (date >= today) return `Today ${time}`;
      if (date >= yesterday) return `Yesterday ${time}`;
      return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
      return new Date(timestamp * 1000).toLocaleDateString();
    }
  };

  /**
   * Format timestamp to precise datetime string
   * Example: "2026-02-15 17:14:23"
   */
  const formatDateTime = (timestamp: number) => {
    try {
      const date = new Date(timestamp * 1000);
      return date.toLocaleString('en-CA', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).replace(',', '');
    } catch {
      return new Date(timestamp * 1000).toLocaleString();
    }
  };

  /**
   * Generate AI summary for a specific email
   */
  const handleSummarize = async (item: FeedItem) => {
    // If already summarized, just toggle expansion
    if (summaries[item.id]) {
      setExpandedId(expandedId === item.id ? null : item.id);
      return;
    }

    // Start summarization
    setSummarizing(prev => ({ ...prev, [item.id]: true }));
    setExpandedId(item.id);

    try {
      const summary = await summarizeEmail(item.id, item.body_preview, item.subject);
      setSummaries(prev => ({ ...prev, [item.id]: summary }));
      toast.success('Summary generated');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Summarization failed';
      toast.error(message);
      setExpandedId(null);
    } finally {
      setSummarizing(prev => ({ ...prev, [item.id]: false }));
    }
  };

  if (appState === 'checking') {
    return (
        <div className="min-h-screen bg-background flex items-center justify-center flex-col gap-6">
            <div className="relative">
                <div className="absolute inset-0 bg-primary blur-xl opacity-20 animate-pulse rounded-full"></div>
                <Sparkles className="w-16 h-16 text-primary animate-pulse relative z-10" />
            </div>
            <p className="text-muted-foreground font-medium tracking-widest uppercase text-sm">Initializing FeedFlux Core</p>
        </div>
    );
  }

  if (appState === 'setup_keys') {
    return (
        <div className="min-h-screen bg-background flex items-center justify-center p-4">
            <Card className="w-full max-w-lg shadow-xl border-border">
                <CardHeader>
                    <div className="flex items-center gap-3 mb-2">
                        <div className="p-2 bg-muted rounded-xl">
                            <Sparkles className="w-6 h-6 text-primary" />
                        </div>
                        <CardTitle className="text-2xl font-bold tracking-tight">Welcome to FeedFlux</CardTitle>
                    </div>
                    <CardDescription className="text-base">To power the AI Intelligence engine and semantic RAG search, please connect your underlying model provider.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 pt-4">
                    <div className="space-y-2">
                        <label className="text-sm font-semibold text-foreground">Google Gemini API Key</label>
                        <Input 
                            type="password" 
                            placeholder="AIzaSy..." 
                            value={apiKeyInput}
                            onChange={e => setApiKeyInput(e.target.value)}
                            className="font-mono text-base py-5"
                        />
                        <p className="text-xs text-muted-foreground pt-1">Your key is stored securely in the local backend `.env` file and never leaves your machine.</p>
                    </div>
                </CardContent>
                <CardFooter className="pt-4">
                    <Button className="w-full text-md py-6 transition-all shadow-md hover:shadow-lg rounded-xl" disabled={!apiKeyInput.trim() || isSettingUp} onClick={handleSetupKeys}>
                        {isSettingUp ? 'Securely Verifying...' : 'Initialize Intelligence Engine'}
                    </Button>
                </CardFooter>
            </Card>
        </div>
    );
  }

  // TODO: change this mock login when we have a real auth system
  if (appState === 'mock_login') {
    return (
        <div className="min-h-screen bg-background flex items-center justify-center p-4">
            <Card className="w-full max-w-lg shadow-2xl border-border">
                <CardHeader className="text-center pb-8 border-b border-border">
                    <div className="mx-auto bg-muted w-24 h-24 rounded-full flex items-center justify-center mb-6 ring-8 ring-background shadow-inner">
                        <svg className="w-12 h-12 text-foreground" viewBox="0 0 24 24" fill="currentColor">
                           <path d="M2 3h20a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm1 2v14h18V5H3zm9 7L3.76 6.3l1.24-.8 7 5.2 7-5.2 1.24.8L12 12z"/>
                        </svg>
                    </div>
                    <CardTitle className="text-3xl font-bold tracking-tight text-foreground">Connect Inbox</CardTitle>
                    <CardDescription className="text-base mt-3 px-4">Securely connect your Microsoft Outlook account to sync your inbox and unlock intelligent AI features.</CardDescription>
                </CardHeader>
                <CardContent className="pt-8 pb-4 px-8">
                    <Button 
                        className="w-full text-base h-12 shadow-md hover:shadow-lg transition-all rounded-xl relative overflow-hidden"
                        onClick={handleMockLogin}
                        disabled={isMockLoggingIn}
                    >
                        {isMockLoggingIn ? (
                            <>
                                <RefreshCw className="mr-3 h-6 w-6 animate-spin" />
                                Authenticating safely...
                            </>
                        ) : (
                            "Login with Microsoft Outlook"
                        )}
                    </Button>
                    <p className="text-center text-xs text-muted-foreground mt-6">By connecting, you agree to local-only processing of your email data.</p>
                </CardContent>
            </Card>
        </div>
    );
  }

  if (appState !== 'feed') {
    return (
      <div className="min-h-screen flex items-center justify-center">
         <p className="text-muted-foreground font-mono">Loading MVP State: [{appState}]...</p>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-background px-4 py-2 font-[family-name:var(--font-geist-sans)]">
      <div className="mx-auto flex h-full w-full max-w-[1800px] flex-col gap-3">
        <header className="shrink-0 rounded-xl border border-border bg-card px-4 py-2 shadow-sm">
          <div className="flex w-full items-center gap-3">
            <h1 className="shrink-0 text-2xl font-bold tracking-tight text-foreground">FeedFlux</h1>

            <div className="relative min-w-0 flex-1 rounded-full shadow-sm">
              <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none">
                <Search className="h-4 w-4 text-muted-foreground" />
              </div>
              <Input
                type="text"
                placeholder="Search by keyword or ask anything to your inbox (e.g. 'What was the Q1 roadmap?')"
                className="w-full rounded-full border-0 bg-muted/50 py-2 pl-11 pr-4 text-sm shadow-none focus-visible:ring-1"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            <Button
              variant="outline"
              className="shrink-0 whitespace-nowrap px-4"
              onClick={() => {
                setIsChatOpen(true);
                if (searchQuery.trim()) {
                  setChatInput(searchQuery);
                  setSearchQuery('');
                }
              }}
            >
              <Sparkles className="w-4 h-4 mr-1.5" />
              Ask AI
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0 text-muted-foreground hover:text-foreground"
              onClick={handleSync}
              disabled={isSyncing}
            >
              <RefreshCw className={`w-4 h-4 mr-1.5 ${isSyncing ? 'animate-spin' : ''}`} />
              {isSyncing ? 'Syncing...' : 'Sync'}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  title="Dev menu"
                >
                  <User className="w-4 h-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => loadFeed(debouncedQuery)}
                  disabled={loading || isSyncing}
                >
                  <RefreshCw className="w-4 h-4" />
                  {loading ? 'Loading...' : 'Reload Local Data'}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 items-stretch gap-3">
          {/* Left column: Feed */}
          <main className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-hidden">

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {/* Daily Briefing Banner */}
          {!debouncedQuery && (
            <div className="mt-4 rounded-xl bg-muted text-foreground border">
              <div className="flex items-center gap-2 p-4 pb-3">
                <Sparkles className="h-5 w-5 text-foreground shrink-0" />
                <h2 className="text-base font-semibold tracking-tight flex-1">Morning Intelligence Briefing</h2>
                <button
                  className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                  onClick={() => setIsBriefingCollapsed(c => !c)}
                  title={isBriefingCollapsed ? 'Expand' : 'Collapse'}
                >
                  {isBriefingCollapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
                </button>
              </div>

              {!isBriefingCollapsed && (
                <div className="px-4 pb-4">
                  {isBriefingLoading ? (
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-3/4 bg-foreground/10" />
                      <Skeleton className="h-4 w-full bg-foreground/10" />
                      <Skeleton className="h-4 w-5/6 bg-foreground/10" />
                    </div>
                  ) : briefingError ? (
                    <div className="bg-destructive/10 p-3 rounded-lg">
                      <div className="text-destructive text-sm">{briefingError}</div>
                    </div>
                  ) : briefing ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none">
                      <ReactMarkdown>{briefing}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">No briefing available today.</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Feed List */}
          <div className="overflow-hidden rounded-lg border border-border/80 bg-card">
            {loading ? (
              // Loading Skeletons
              Array.from({ length: 3 }).map((_, i) => (
                <Card key={i} className="w-full">
                  <CardHeader>
                    <Skeleton className="h-6 w-2/3 mb-2" />
                    <Skeleton className="h-4 w-1/3" />
                  </CardHeader>
                  <CardContent>
                    <Skeleton className="h-4 w-full mb-2" />
                    <Skeleton className="h-4 w-4/5" />
                  </CardContent>
                </Card>
              ))
            ) : error ? (
              // Error State
              <Card className="border-destructive/50 bg-destructive/10">
                <CardHeader>
                  <CardTitle className="text-destructive">Failed to Load Feed</CardTitle>
                  <CardDescription>{error}</CardDescription>
                </CardHeader>
                <CardFooter>
                  <Button onClick={() => loadFeed(debouncedQuery)} variant="outline">Try Again</Button>
                </CardFooter>
              </Card>
            ) : feed.length === 0 ? (
              // Empty State
              <Card className="border-dashed">
                <CardHeader>
                  <CardTitle className="text-muted-foreground">No Emails Found</CardTitle>
                  <CardDescription>Your inbox is empty or all emails have been processed.</CardDescription>
                </CardHeader>
              </Card>
            ) : (
              // Email Cards
              feed.map((item) => {
                const isSummarizing = summarizing[item.id];
                const summary = summaries[item.id];
                const isExpanded = expandedId === item.id;
                const senderLabel = item.sender || 'Unknown sender';
                const avatar = getAvatarPresentation(senderLabel);

                return (
                  <Card
                    key={item.id}
                    onClick={() => handleOpenEmailDetail(item.id)}
                    className="group cursor-pointer rounded-none border-0 border-b border-border last:border-b-0 border-l-2 border-l-transparent gap-0 py-0 shadow-none transition-colors hover:border-l-primary hover:bg-accent/40"
                  >
                    <CardHeader className="relative flex flex-row items-center gap-3 px-3 py-2.5">
                      <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${avatar.background} ${avatar.foreground}`}>
                        {avatar.initials}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-2">
                          <CardDescription className="max-w-[30%] shrink-0 truncate text-xs font-medium text-foreground">
                            {senderLabel}
                          </CardDescription>
                          <CardTitle className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
                            {item.subject}
                          </CardTitle>
                        </div>
                        <CardDescription className="mt-0.5 truncate text-xs text-muted-foreground">
                          {item.body_preview}
                        </CardDescription>
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground transition-opacity group-hover:opacity-0">
                        {formatTime(item.received_datetime)}
                      </span>
                      <div className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md bg-card opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" onClick={(e) => e.stopPropagation()}>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="text-primary opacity-0 transition-all group-hover:opacity-100 focus-visible:opacity-100 hover:bg-background hover:shadow-sm"
                          onClick={() => handleSummarize(item)}
                          disabled={isSummarizing}
                          aria-label={summary ? (isExpanded ? 'Hide summary' : 'Show summary') : 'Summarize email'}
                          title={summary ? (isExpanded ? 'Hide summary' : 'Show summary') : 'Summarize email'}
                        >
                          {isSummarizing ? <Loader2 className="animate-spin" /> : summary ? (isExpanded ? <ChevronUp /> : <ChevronDown />) : <Sparkles />}
                        </Button>
                      </div>
                    </CardHeader>
                    {isExpanded && (
                      <CardContent className="bg-muted/20 px-3 py-2" onClick={(e) => e.stopPropagation()}>
                        {isSummarizing ? (
                          <div className="space-y-2">
                            <Skeleton className="h-4 w-full" />
                            <Skeleton className="h-4 w-5/6" />
                            <Skeleton className="h-4 w-4/6" />
                          </div>
                        ) : summary ? (
                          <div className="space-y-2">
                            {/* Generation Metadata: AI + Model */}
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className="text-xs">
                                AI Summary
                              </Badge>
                              {summary.model && (
                                <span className="text-xs text-muted-foreground">
                                  by {summary.model}
                                </span>
                              )}
                            </div>
                            <div className="text-xs leading-4 text-muted-foreground [&_p]:my-0 [&_h1]:text-xs [&_h2]:text-xs [&_h3]:text-xs [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0">
                              <ReactMarkdown>{summary.summary}</ReactMarkdown>
                            </div>
                            {/* Related count (left) + last generated time (bottom-right) */}
                            <div className="flex items-center gap-2 flex-wrap pt-1">
                              {summary.context_count > 0 && (
                                <span className="text-xs text-muted-foreground">
                                  Found {summary.context_count} related email{summary.context_count > 1 ? 's' : ''}
                                </span>
                              )}
                              {summary.generated_at && (
                                <span className="text-xs text-muted-foreground ml-auto">
                                  Last generated: {formatDateTime(summary.generated_at)}
                                </span>
                              )}
                            </div>
                          </div>
                        ) : null}
                      </CardContent>
                    )}
                  </Card>
                );
              })
            )}
          </div>
          </div>
        </main>

        {/* Right column: AI Sidebar (Multi-Turn Chat) */}
        {isChatOpen && (
          <aside className="order-2 flex h-full min-h-0 w-[450px] shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
            {/* Header */}
            <div className="p-4 border-b border-border bg-muted/30 flex justify-between items-center shrink-0">
              <div className="flex items-center gap-2">
                <div className="p-1.5 bg-muted rounded-md">
                  <Sparkles className="w-4 h-4 text-foreground" />
                </div>
                <h2 className="text-sm font-semibold text-foreground">Inbox QA Assistant</h2>
              </div>
              <div className="flex items-center gap-1">
                {chatMessages.length > 0 && (
                  <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors" onClick={handleNewChat} title="New chat (clears history and resets thread)">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
                <Button variant="ghost" size="icon" className="h-8 w-8 -mr-2 text-muted-foreground hover:text-foreground" onClick={() => setIsChatOpen(false)}>
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>

            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-5 relative">
              <div className="space-y-6 pb-2">
                {chatMessages.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-center space-y-4 pt-20">
                    <div className="p-4 bg-muted rounded-full">
                      <Sparkles className="w-8 h-8 text-muted-foreground" />
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-foreground mb-1">How can I help you today?</h3>
                      <p className="text-sm text-muted-foreground max-w-[250px] mx-auto">Ask me to find specific emails, summarize threads, or extract information from your inbox.</p>
                    </div>
                  </div>
                ) : (
                  chatMessages.map(msg => (
                    <div key={msg.id} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'} gap-1.5`}>
                      <span className="text-[11px] font-medium text-muted-foreground px-1">{msg.role === 'user' ? 'You' : 'AI Assistant'}</span>

                      {msg.role === 'user' ? (
                        <div className="px-4 py-3 max-w-[90%] text-sm bg-primary text-primary-foreground rounded-2xl rounded-tr-sm">
                          <div className="prose prose-sm dark:prose-invert prose-p:leading-snug max-w-none">
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                          </div>
                        </div>
                      ) : (
                        (msg.segments?.length || msg.isLoading) && (
                          <div className="max-w-[95%] text-sm text-foreground">
                            {pairSegments(msg.segments ?? []).map((item) =>
                              item.kind === 'text' ? (
                                <div key={item.key} className="prose prose-sm dark:prose-invert prose-p:leading-snug prose-p:my-2 max-w-none">
                                  <ReactMarkdown>{item.text}</ReactMarkdown>
                                </div>
                              ) : (
                                <ToolCallLine
                                  key={item.key}
                                  tool={item.tool}
                                  args={item.args}
                                  output={item.output}
                                  resultCount={item.resultCount}
                                  running={item.running}
                                />
                              )
                            )}
                            {msg.isLoading && (
                              <div className="flex gap-1 pt-1">
                                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: '0ms' }} />
                                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: '150ms' }} />
                                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: '300ms' }} />
                              </div>
                            )}
                          </div>
                        )
                      )}

                      {msg.role === 'assistant' && msg.pendingInterrupt && (
                        msg.pendingInterrupt.tool === 'send_reply' ? (
                          <MeetingReplyReviewCard
                            interrupt={msg.pendingInterrupt}
                            disabled={isSendingChat}
                            onDecide={(approve, note, editedBody) => handleResume(msg.id, approve, note, editedBody)}
                          />
                        ) : (
                          <InterruptApprovalCard
                            interrupt={msg.pendingInterrupt}
                            disabled={isSendingChat}
                            onDecide={(approve, note) => handleResume(msg.id, approve, note)}
                          />
                        )
                      )}

                      {msg.role === 'assistant' && msg.triagePlan && (
                        <BatchTriageReviewCard
                          plan={msg.triagePlan}
                          threadId={threadId}
                          onView={handleOpenEmailDetail}
                          onDraft={handleDraftFromTriage}
                        />
                      )}

                      {msg.role === 'assistant'
                        && msg.segments?.some(s => s.kind === 'tool_end' && s.tool === 'send_reply') && (
                        <SentDryRunChip />
                      )}

                      {/* Citations/Sources Cards attached to AI Response */}
                      {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5 w-[90%]">
                          {msg.sources.map((source, i) => (
                            <button
                              key={i}
                              onClick={() => handleOpenEmailDetail(source.id)}
                              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium bg-card border border-border rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground hover:border-primary transition-colors shadow-sm max-w-full text-left"
                              title={source.snippet}
                            >
                              <span className="text-primary font-semibold whitespace-nowrap">Source {i + 1}</span>
                              <span className="truncate max-w-[150px]">{source.subject}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Input Overlay / Footer */}
            <div className="p-4 bg-card border-t border-border shrink-0">
              <form onSubmit={handleSendChatMessage} className="relative flex items-center">
                <Input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  disabled={isSendingChat}
                  placeholder="Ask a follow-up question..."
                  className="w-full pr-12 rounded-full shadow-sm"
                />
                <Button
                  type="submit"
                  disabled={!chatInput.trim() || isSendingChat}
                  size="icon"
                  variant="ghost"
                  className="absolute right-1 text-primary h-8 w-8 rounded-full"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </form>
            </div>
          </aside>
        )}
        {/* Right column: Email reading pane (master-detail) */}
        <section className="order-1 flex h-full min-h-0 min-w-0 flex-[1.4] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
            {emailDetailData || isLoadingDetail ? (
              <>
                <div className="shrink-0 border-b border-border bg-muted/30 p-6">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="text-xl font-semibold text-foreground">
                      {emailDetailData?.subject || "Loading..."}
                    </h2>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="-mr-2 h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
                      onClick={handleCloseEmailDetail}
                      title="Close"
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                  {emailDetailData && (
                    <div className="mt-2 flex items-center justify-between text-sm text-muted-foreground">
                      <div className="flex min-w-0 items-center gap-2">
                        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${detailAvatar?.background} ${detailAvatar?.foreground}`}>
                          {detailAvatar?.initials}
                        </div>
                        <span className="truncate">From: <span className="font-medium text-foreground">{emailDetailData.sender}</span></span>
                      </div>
                      <span>{formatDateTime(emailDetailData.received_datetime)}</span>
                    </div>
                  )}
                </div>
                {/* Resizable Container wrapping Body & Action Panel */}
                <div className="relative flex min-h-0 min-w-0 w-full flex-1 overflow-hidden bg-muted">
                  <ResizablePanelGroup id="email-detail-group" orientation="vertical">

              {/* TOP PANEL: Original Email */}
              <ResizablePanel id="email-body-panel" defaultSize={78} minSize={45} className="bg-background flex flex-col relative pb-4">
                <div className="flex-1 overflow-y-auto w-full p-6">
                  {isLoadingDetail ? (
                    <div className="space-y-4">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-[95%]" />
                      <Skeleton className="h-4 w-[90%]" />
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-[85%]" />
                      <Skeleton className="h-4 w-[90%]" />
                    </div>
                  ) : emailDetailData ? (
                    <div className="prose prose-sm md:prose-base dark:prose-invert max-w-none text-foreground">
                      {emailDetailData.body_html ? (
                        <div dangerouslySetInnerHTML={{ __html: emailDetailData.body_html }} />
                      ) : (
                        <div className="whitespace-pre-wrap">{emailDetailData.body_content}</div>
                      )}
                    </div>
                  ) : (
                    <div className="text-center text-destructive">Failed to load email content.</div>
                  )}
                </div>
              </ResizablePanel>

              {/* DRAGGABLE DIVIDER */}
              <ResizableHandle id="email-divider" withHandle className="hover:bg-primary hover:h-1.5 transition-all outline-none relative group/handle" />

              {/* BOTTOM PANEL: AI Action Panel (Draft Reply) */}
              <ResizablePanel
                id="email-action-panel"
                defaultSize={22}
                minSize={12}
                className="bg-muted/30 flex flex-col relative border-t border-border"
              >
                {draftTabs.length > 0 && (
                  <div className="scrollbar-none flex min-h-9 shrink-0 touch-pan-x items-center gap-1 overflow-x-auto overscroll-x-contain border-b border-border/60 bg-background/70 px-2 py-1">
                    <span className="shrink-0 px-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">
                      Drafts
                    </span>
                    {draftTabs.map(({ emailId, draft, subject }) => {
                      const active = emailId === activeEmailId && focusedDraftByEmailId[emailId] === draft.id;
                      return (
                        <button
                          key={`${emailId}-${draft.id}`}
                          type="button"
                          onClick={() => handleDraftTabSelect(emailId, draft.id)}
                          className={`flex h-7 max-w-[220px] shrink-0 select-none items-center gap-1 rounded-md px-2.5 text-[11px] transition-colors ${active ? 'bg-muted text-foreground' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}
                          title={`${subject} · Edited ${new Date(draft.updated_at * 1000).toLocaleString()}`}
                        >
                          <Pencil className="h-3 w-3 shrink-0" />
                          <span className="max-w-[140px] truncate">{subject}</span>
                          <span className="shrink-0 text-[10px] text-muted-foreground/70">
                            {new Date(draft.updated_at * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {mountedEmailIds.map((id) => {
                  const detail = emailDetailsById[id];
                  if (!detail) return null;
                  const active = id === activeEmailId && !isLoadingDetail;
                  return (
                    <div key={id} className={active ? 'flex h-full w-full' : 'hidden'}>
                      <DraftWorkspace
                        emailId={detail.id}
                        subject={detail.subject}
                        sender={detail.sender || detail.sender_email}
                        senderEmail={detail.sender_email}
                        autoDraft={active && autoDraftOnOpen}
                        focusDraftId={focusedDraftByEmailId[id]}
                        onDraftsChange={(drafts) => handleDraftsChange(id, drafts)}
                        onDraftFocus={(draftId) => handleDraftFocus(id, draftId)}
                      />
                    </div>
                  );
                })}
                {(!emailDetailData || isLoadingDetail) && (
                  <div className="flex h-full w-full flex-col items-center justify-center space-y-3 text-muted-foreground">
                    <Sparkles className="h-8 w-8 animate-pulse opacity-20" />
                    <p className="text-sm font-medium">Preparing AI Assistant...</p>
                  </div>
                )}
              </ResizablePanel>
                  </ResizablePanelGroup>
                </div>
              </>
            ) : (
              <div className="flex min-h-0 flex-1 items-center justify-center bg-background p-6 text-center">
                <div className="flex max-w-xs flex-col items-center gap-3 text-muted-foreground">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
                    <Mail className="h-8 w-8" />
                  </div>
                  <h2 className="text-base font-medium text-foreground">No Conversation Selected</h2>
                  <p className="text-sm">Select an email from your inbox to read it here.</p>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div >
  );
}
