'use client';

import { useEffect, useLayoutEffect, useRef, useState, type UIEvent } from 'react';
import { Loader2, Pencil, Send, Sparkles, Trash2, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  askAgentStream,
  createReplyDraft,
  discardDraft,
  getEmailDrafts,
  sendDraft,
  updateDraft,
  type AgentStreamCallbacks,
  type DraftReply,
} from '@/lib/api';

interface DraftWorkspaceProps {
  emailId: string;
  subject: string;
  sender: string;
  senderEmail?: string;
  autoDraft?: boolean;
}

const intents = [
  { label: '👍 Sounds good', prompt: 'Sounds good / Agree / Acknowledge' },
  { label: '✋ Polite Decline', prompt: 'Polite Decline / Cannot attend' },
  { label: '❓ Need info', prompt: 'Need more info / Ask for details' },
];

function formatDraftTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function DraftWorkspace({ emailId, subject, sender, senderEmail, autoDraft = false }: DraftWorkspaceProps) {
  const [drafts, setDrafts] = useState<DraftReply[]>([]);
  const [editingDraftId, setEditingDraftId] = useState<number | null>(null);
  const [customPrompt, setCustomPrompt] = useState('');
  const [selectionPrompt, setSelectionPrompt] = useState('');
  const [selection, setSelection] = useState<{ draftId: number; start: number; end: number; text: string } | null>(null);
  const [selectionPosition, setSelectionPosition] = useState<{ left: number; top: number } | null>(null);
  const [recentChange, setRecentChange] = useState<{ draftId: number; start: number; end: number } | null>(null);
  const [recentChangeFading, setRecentChangeFading] = useState(false);
  const [undoState, setUndoState] = useState<{ draftId: number; body: string } | null>(null);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const [isDrafting, setIsDrafting] = useState(false);
  const [isCreatingManualDraft, setIsCreatingManualDraft] = useState(false);
  const [showAiTools, setShowAiTools] = useState(false);
  const [busyDraftId, setBusyDraftId] = useState<number | null>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const textareaRefs = useRef<Record<number, HTMLTextAreaElement | null>>({});
  const highlightRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const saveTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const autoDraftedFor = useRef<string | null>(null);
  const selectionAnchorRef = useRef<{ left: number; top: number; bottom: number } | null>(null);
  const selectionMeasureRef = useRef<{ draftId: number; body: string; end: number } | null>(null);
  const selectionMeasureRafRef = useRef<number | null>(null);
  const toolbarPositionRafRef = useRef<number | null>(null);
  const recentChangeTimerRef = useRef<number | null>(null);
  const recentChangeFadeTimerRef = useRef<number | null>(null);
  const selectionNoticeTimerRef = useRef<number | null>(null);
  const undoTimerRef = useRef<number | null>(null);

  const clearUndo = () => {
    setUndoState(null);
    if (undoTimerRef.current !== null) {
      window.clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
  };

  const clearSelection = () => {
    selectionAnchorRef.current = null;
    setSelection(null);
    setSelectionPosition(null);
    setSelectionPrompt('');
    setSelectionNotice(null);
    if (selectionNoticeTimerRef.current !== null) {
      window.clearTimeout(selectionNoticeTimerRef.current);
      selectionNoticeTimerRef.current = null;
    }
  };

  const clampToolbarPosition = (
    anchor: { left: number; top: number; bottom: number },
    workspace: HTMLDivElement,
    toolbarWidth: number,
    toolbarHeight: number,
  ) => {
    const minLeft = workspace.scrollLeft + 8;
    const maxLeft = Math.max(minLeft, workspace.scrollLeft + workspace.clientWidth - toolbarWidth - 8);
    const left = Math.min(Math.max(anchor.left, minLeft), maxLeft);
    const minTop = workspace.scrollTop + 8;
    const maxTop = Math.max(minTop, workspace.scrollTop + workspace.clientHeight - toolbarHeight - 8);
    const below = anchor.bottom + 8;
    const above = anchor.top - toolbarHeight - 8;
    const top = below <= maxTop
      ? below
      : above >= minTop
        ? above
        : Math.min(Math.max(below, minTop), maxTop);
    return { left, top };
  };

  const positionToolbar = () => {
    const workspace = workspaceRef.current;
    const anchor = selectionAnchorRef.current;
    if (!workspace || !anchor) return;
    const toolbar = toolbarRef.current;
    const nextPosition = clampToolbarPosition(
      anchor,
      workspace,
      toolbar?.offsetWidth ?? 280,
      toolbar?.offsetHeight ?? 48,
    );
    setSelectionPosition((current) => (
      current?.left === nextPosition.left && current.top === nextPosition.top ? current : nextPosition
    ));
  };

  const scheduleToolbarPosition = () => {
    if (toolbarPositionRafRef.current !== null) return;
    toolbarPositionRafRef.current = requestAnimationFrame(() => {
      toolbarPositionRafRef.current = null;
      positionToolbar();
    });
  };

  const loadDrafts = async (selectId?: number): Promise<DraftReply[]> => {
    try {
      const nextDrafts = await getEmailDrafts(emailId);
      setDrafts(nextDrafts);
      if (selectId && nextDrafts.some((draft) => draft.id === selectId)) {
        setEditingDraftId(selectId);
      }
      return nextDrafts;
    } catch {
      toast.error('Failed to load reply drafts.');
      return [];
    }
  };

  useEffect(() => {
    setDrafts([]);
    setEditingDraftId(null);
    clearSelection();
    setRecentChange(null);
    setRecentChangeFading(false);
    clearUndo();
    setShowAiTools(false);
    if (recentChangeTimerRef.current !== null) {
      window.clearTimeout(recentChangeTimerRef.current);
      recentChangeTimerRef.current = null;
    }
    if (recentChangeFadeTimerRef.current !== null) {
      window.clearTimeout(recentChangeFadeTimerRef.current);
      recentChangeFadeTimerRef.current = null;
    }
    void loadDrafts();
    return () => {
      Object.values(saveTimers.current).forEach(clearTimeout);
      saveTimers.current = {};
    };
  }, [emailId]);

  useEffect(() => {
    const clearSelectionOnPointerDown = (event: PointerEvent) => {
      if (!selection) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest('[data-selection-toolbar]') || target.closest('textarea')) return;
      clearSelection();
    };
    document.addEventListener('pointerdown', clearSelectionOnPointerDown);
    return () => document.removeEventListener('pointerdown', clearSelectionOnPointerDown);
  }, [selection]);

  useLayoutEffect(() => {
    if (selectionPosition) positionToolbar();
  }, [selectionPosition, selection]);

  useEffect(() => {
    if (!selection) return;
    const handleResize = () => {
      const draft = drafts.find((item) => item.id === selection.draftId);
      if (draft) scheduleSelectionMeasurement(selection.draftId, draft.body, selection.end);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [selection, drafts]);

  useEffect(() => () => {
    if (selectionMeasureRafRef.current !== null) cancelAnimationFrame(selectionMeasureRafRef.current);
    if (toolbarPositionRafRef.current !== null) cancelAnimationFrame(toolbarPositionRafRef.current);
    if (recentChangeTimerRef.current !== null) window.clearTimeout(recentChangeTimerRef.current);
    if (recentChangeFadeTimerRef.current !== null) window.clearTimeout(recentChangeFadeTimerRef.current);
    if (selectionNoticeTimerRef.current !== null) window.clearTimeout(selectionNoticeTimerRef.current);
    if (undoTimerRef.current !== null) window.clearTimeout(undoTimerRef.current);
  }, []);

  const requestDraft = async (intent: string) => {
    setShowAiTools(true);
    setIsDrafting(true);
    const target = senderEmail ? `${sender} <${senderEmail}>` : sender;
    const currentDraft = drafts.find((draft) => draft.id === editingDraftId) ?? drafts[0];
    const prompt = currentDraft ? [
      `Revise existing draft ${currentDraft.id} for the email with id "${emailId}".`,
      `Find the email from ${target} with subject "${subject}" and use that exact email id when calling send_reply.`,
      `Keep the same recipient and subject, improve the current draft below, and call send_reply with draft_id ${currentDraft.id}.`,
      `Current draft body:\n${currentDraft.body}`,
      `Revision intent: ${intent}.`,
      customPrompt.trim() ? `Additional instructions: ${customPrompt.trim()}` : '',
    ].filter(Boolean).join(' ') : [
      `Draft a reply for the email with id "${emailId}".`,
      `Find the email from ${target} with subject "${subject}" and use that exact email id when calling send_reply.`,
      `Reply intent: ${intent}.`,
      customPrompt.trim() ? `Additional instructions: ${customPrompt.trim()}` : '',
    ].filter(Boolean).join(' ');

    const callbacks: AgentStreamCallbacks = {
      onTrace: () => {},
      onToken: () => {},
      onInterrupt: () => {},
      onDraft: (event) => {
        if (event.email_id === emailId) {
          void loadDrafts(event.draft_id);
          toast.success('Draft ready for review.');
        }
      },
      onDone: () => setIsDrafting(false),
      onError: (message) => {
        setIsDrafting(false);
        toast.error(message || 'Failed to draft a reply.');
      },
    };

    try {
      await askAgentStream(crypto.randomUUID(), prompt, callbacks);
    } catch {
      setIsDrafting(false);
      toast.error('Failed to reach the drafting agent.');
    }
  };

  useEffect(() => {
    if (!autoDraft) {
      autoDraftedFor.current = null;
      return;
    }
    if (autoDraftedFor.current === emailId) return;
    autoDraftedFor.current = emailId;
    void requestDraft('Follow the request in the batch triage card');
  }, [emailId, autoDraft]);

  const handleBodyChange = (draftId: number, body: string) => {
    if (undoState?.draftId === draftId) clearUndo();
    setDrafts((current) => current.map((draft) => draft.id === draftId ? {
      ...draft,
      body,
      updated_at: Math.floor(Date.now() / 1000),
    } : draft));
    const existing = saveTimers.current[draftId];
    if (existing) clearTimeout(existing);
    saveTimers.current[draftId] = setTimeout(() => {
      void updateDraft(draftId, body).catch(() => toast.error('Draft autosave failed.'));
    }, 600);
  };

  const handleManualReply = async () => {
    setIsCreatingManualDraft(true);
    try {
      const draft = await createReplyDraft(emailId);
      await loadDrafts(draft.id);
    } catch {
      toast.error('Failed to start a reply draft.');
    } finally {
      setIsCreatingManualDraft(false);
    }
  };

  const handleTextareaScroll = (draftId: number, event: UIEvent<HTMLTextAreaElement>) => {
    const highlight = highlightRefs.current[draftId];
    if (highlight) {
      highlight.style.transform = `translate(${-event.currentTarget.scrollLeft}px, ${-event.currentTarget.scrollTop}px)`;
    }
    if (selection?.draftId === draftId) {
      const draft = drafts.find((item) => item.id === draftId);
      if (draft) scheduleSelectionMeasurement(draftId, draft.body, selection.end);
    }
  };

  const measureSelection = (draftId: number, body: string, end: number) => {
    const textarea = textareaRefs.current[draftId];
    const mirror = mirrorRef.current;
    const workspace = workspaceRef.current;
    if (!textarea || !mirror || !workspace) return;
    const style = getComputedStyle(textarea);
    mirror.style.width = `${textarea.clientWidth}px`;
    mirror.style.left = `${textarea.getBoundingClientRect().left - textarea.scrollLeft}px`;
    mirror.style.top = `${textarea.getBoundingClientRect().top - textarea.scrollTop}px`;
    mirror.style.font = style.font;
    mirror.style.letterSpacing = style.letterSpacing;
    mirror.style.lineHeight = style.lineHeight;
    mirror.style.padding = style.padding;
    mirror.style.border = style.border;
    mirror.style.boxSizing = 'border-box';
    mirror.textContent = body.slice(0, end);
    const marker = document.createElement('span');
    marker.textContent = '\u200b';
    mirror.appendChild(marker);
    const markerRect = marker.getBoundingClientRect();
    const workspaceRect = workspace.getBoundingClientRect();
    const anchor = {
      left: markerRect.left - workspaceRect.left + workspace.scrollLeft,
      top: markerRect.top - workspaceRect.top + workspace.scrollTop,
      bottom: markerRect.bottom - workspaceRect.top + workspace.scrollTop,
    };
    selectionAnchorRef.current = anchor;
    const nextPosition = clampToolbarPosition(
      anchor,
      workspace,
      toolbarRef.current?.offsetWidth ?? 280,
      toolbarRef.current?.offsetHeight ?? 48,
    );
    setSelectionPosition(nextPosition);
  };

  const scheduleSelectionMeasurement = (draftId: number, body: string, end: number) => {
    selectionMeasureRef.current = { draftId, body, end };
    if (selectionMeasureRafRef.current !== null) return;
    selectionMeasureRafRef.current = requestAnimationFrame(() => {
      selectionMeasureRafRef.current = null;
      const pending = selectionMeasureRef.current;
      if (pending) measureSelection(pending.draftId, pending.body, pending.end);
    });
  };

  const handleSelection = (draftId: number, body: string, start: number, end: number) => {
    if (start === end) {
      if (selection?.draftId === draftId) return;
      clearSelection();
      return;
    }
    setSelection({ draftId, start, end, text: body.slice(start, end) });
    scheduleSelectionMeasurement(draftId, body, end);
  };

  const requestSelectionRewrite = async (draft: DraftReply) => {
    if (!selection || selection.draftId !== draft.id || !selectionPrompt.trim()) return;
    setBusyDraftId(draft.id);
    setSelectionNotice(null);
    try {
      await updateDraft(draft.id, draft.body);
      const target = senderEmail ? `${sender} <${senderEmail}>` : sender;
      const prompt = [
        `Rewrite only the selected text in draft ${draft.id} for the email with id "${emailId}".`,
        `The email is from ${target} with subject "${subject}".`,
        `Selection offsets are ${selection.start}-${selection.end}; selected text is:\n${selection.text}`,
        `Instruction: ${selectionPrompt.trim()}.`,
        'If the instruction is not a concrete, actionable rewrite request, do not call any draft tool and explain that no change was made. Otherwise call apply_draft_patch with the same draft_id and offsets. Return replacement text only in the replacement field; preserve every other character.',
      ].join(' ');
      let didUpdateDraft = false;
      const callbacks: AgentStreamCallbacks = {
        onTrace: () => {},
        onToken: () => {},
        onInterrupt: () => {},
        onDraft: (event) => {
          if (event.email_id === emailId && event.draft_id === draft.id) {
            didUpdateDraft = true;
            const previousSelection = selection;
            const previousBody = draft.body;
            void loadDrafts(draft.id).then((nextDrafts) => {
              const updatedDraft = nextDrafts.find((item) => item.id === draft.id);
              if (updatedDraft && previousSelection) {
                clearUndo();
                setUndoState({ draftId: draft.id, body: previousBody });
                undoTimerRef.current = window.setTimeout(() => {
                  setUndoState(null);
                  undoTimerRef.current = null;
                }, 8000);
                const unchangedSuffixLength = previousBody.length - previousSelection.end;
                const changedEnd = Math.max(
                  previousSelection.start,
                  updatedDraft.body.length - unchangedSuffixLength,
                );
                setRecentChange({
                  draftId: draft.id,
                  start: previousSelection.start,
                  end: changedEnd,
                });
                setRecentChangeFading(false);
                if (recentChangeFadeTimerRef.current !== null) {
                  window.clearTimeout(recentChangeFadeTimerRef.current);
                }
                recentChangeFadeTimerRef.current = window.setTimeout(() => {
                  setRecentChangeFading(true);
                  recentChangeFadeTimerRef.current = null;
                }, 50);
                if (recentChangeTimerRef.current !== null) {
                  window.clearTimeout(recentChangeTimerRef.current);
                }
                recentChangeTimerRef.current = window.setTimeout(() => {
                  setRecentChange(null);
                  setRecentChangeFading(false);
                  recentChangeTimerRef.current = null;
                }, 800);
              }
              clearSelection();
            });
          }
        },
        onDone: () => {
          setBusyDraftId(null);
          if (!didUpdateDraft) {
            setSelectionNotice('No changes made');
            if (selectionNoticeTimerRef.current !== null) {
              window.clearTimeout(selectionNoticeTimerRef.current);
            }
            selectionNoticeTimerRef.current = window.setTimeout(() => {
              setSelectionNotice(null);
              selectionNoticeTimerRef.current = null;
            }, 1600);
          }
        },
        onError: (message) => {
          setBusyDraftId(null);
          toast.error(message || 'Failed to revise selected text.');
        },
      };
      await askAgentStream(crypto.randomUUID(), prompt, callbacks);
    } catch {
      setBusyDraftId(null);
      toast.error('Failed to reach the drafting agent.');
    }
  };

  const handleUndo = async (draftId: number) => {
    if (!undoState || undoState.draftId !== draftId) return;
    setBusyDraftId(draftId);
    try {
      const restored = await updateDraft(draftId, undoState.body);
      setDrafts((current) => current.map((draft) => draft.id === draftId ? restored : draft));
      clearUndo();
      setRecentChange(null);
      setRecentChangeFading(false);
    } catch {
      toast.error('Failed to undo AI rewrite.');
    } finally {
      setBusyDraftId(null);
    }
  };

  const handleSend = async (draftId: number) => {
    setBusyDraftId(draftId);
    try {
      await sendDraft(draftId);
      if (undoState?.draftId === draftId) clearUndo();
      setDrafts((current) => current.filter((draft) => draft.id !== draftId));
      if (editingDraftId === draftId) setEditingDraftId(null);
      toast.success('Reply recorded as sent (dry-run).');
    } catch {
      toast.error('Failed to send draft.');
    } finally {
      setBusyDraftId(null);
    }
  };

  const handleDiscard = async (draftId: number) => {
    setBusyDraftId(draftId);
    try {
      await discardDraft(draftId);
      if (undoState?.draftId === draftId) clearUndo();
      setDrafts((current) => current.filter((draft) => draft.id !== draftId));
      if (editingDraftId === draftId) setEditingDraftId(null);
      toast.success('Draft discarded.');
    } catch {
      toast.error('Failed to discard draft.');
    } finally {
      setBusyDraftId(null);
    }
  };

  return (
      <div ref={workspaceRef} onScroll={scheduleToolbarPosition} className="relative flex h-full w-full min-w-0 flex-col gap-3 overflow-y-auto p-6">
      <div className="flex w-full shrink-0 items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Pencil className="h-5 w-5 text-primary" />
          <h3 className="font-semibold text-foreground">Reply</h3>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {drafts.length === 0 && !isDrafting && (
            <Button size="sm" onClick={() => void handleManualReply()} disabled={isCreatingManualDraft}>
              <Pencil className="h-3.5 w-3.5" /> Reply
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAiTools((visible) => !visible)}
            disabled={isDrafting}
            className="transition-all hover:bg-accent hover:shadow-md"
          >
            <Sparkles className="h-3.5 w-3.5" /> {showAiTools ? 'Hide AI' : 'Help me write'}
          </Button>
        </div>
      </div>

      {showAiTools && (
        <div className="w-full shrink-0 rounded-lg border border-border/70 bg-background/60 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" /> Help me write
          </div>
          <div className="flex flex-wrap gap-2">
            {intents.map((intent) => (
              <Button key={intent.prompt} variant="outline" size="sm" onClick={() => void requestDraft(intent.prompt)} disabled={isDrafting}>
                {intent.label}
              </Button>
            ))}
            <div className="flex min-w-[200px] flex-1 gap-2">
              <Input
                placeholder={editingDraftId ? 'Ask AI to revise this draft...' : 'Or type custom instructions...'}
                value={customPrompt}
                onChange={(event) => setCustomPrompt(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && void requestDraft('Follow custom instructions')}
                className="h-9 bg-background"
                disabled={isDrafting}
              />
              <Button variant="outline" size="sm" onClick={() => void requestDraft('Follow custom instructions')} disabled={isDrafting}>
                Generate
              </Button>
            </div>
          </div>
        </div>
      )}

      {isDrafting && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-background p-3 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Drafting in the email workflow...
        </div>
      )}

      {drafts.length === 0 && !isDrafting ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          No active drafts for this email.
        </div>
      ) : (
        <div className="space-y-3">
          {drafts.length > 0 && (
            <div className="text-xs text-muted-foreground">
              AI actions revise this saved draft instead of creating another version.
            </div>
          )}
          {drafts.map((draft) => {
            const editing = editingDraftId === draft.id;
            const busy = busyDraftId === draft.id;
            const highlight = selection?.draftId === draft.id
              ? { start: selection.start, end: selection.end, recent: false }
              : recentChange?.draftId === draft.id
                ? { start: recentChange.start, end: recentChange.end, recent: true }
                : null;
            return (
              <div key={draft.id} className="rounded-lg border border-border bg-background p-3 shadow-sm">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    Edited {formatDraftTime(draft.updated_at)}
                  </span>
                  <div className="flex items-center gap-1">
                    {undoState?.draftId === draft.id && (
                      <Button variant="ghost" size="sm" onClick={() => void handleUndo(draft.id)} disabled={busy}>
                        <Undo2 className="h-3.5 w-3.5" /> Undo
                      </Button>
                    )}
                    {!editing && (
                      <Button variant="ghost" size="sm" onClick={() => setEditingDraftId(draft.id)} disabled={busy}>
                        <Pencil className="h-3.5 w-3.5" /> Edit
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => void handleDiscard(draft.id)} disabled={busy}>
                      <Trash2 className="h-3.5 w-3.5" /> Discard
                    </Button>
                    <Button size="sm" onClick={() => void handleSend(draft.id)} disabled={busy}>
                      <Send className="h-3.5 w-3.5" /> Send
                    </Button>
                  </div>
                </div>
                {editing ? (
                  <div className="relative rounded-md bg-muted/20">
                    <div className="pointer-events-none absolute inset-px overflow-hidden rounded-[5px]" aria-hidden="true">
                      <div
                        ref={(element) => { highlightRefs.current[draft.id] = element; }}
                        className="whitespace-pre-wrap break-words px-3 py-2 text-base text-transparent md:text-sm"
                      >
                        {highlight ? (
                          <>
                            {draft.body.slice(0, highlight.start)}
                            <mark className={highlight.recent
                              ? `${recentChangeFading ? 'bg-transparent' : 'bg-emerald-300/80 dark:bg-emerald-500/60'} text-transparent transition-colors duration-700`
                              : 'bg-blue-200/80 dark:bg-blue-500/50 text-transparent'}>
                              {draft.body.slice(highlight.start, highlight.end)}
                            </mark>
                            {draft.body.slice(highlight.end)}
                          </>
                        ) : draft.body}
                      </div>
                    </div>
                    <Textarea
                      ref={(element) => { textareaRefs.current[draft.id] = element; }}
                      value={draft.body}
                      onChange={(event) => handleBodyChange(draft.id, event.target.value)}
                      onMouseDown={() => {
                        if (selection?.draftId !== draft.id) return;
                        clearSelection();
                      }}
                      onSelect={(event) => handleSelection(
                        draft.id,
                        draft.body,
                        event.currentTarget.selectionStart,
                        event.currentTarget.selectionEnd,
                      )}
                      onScroll={(event) => handleTextareaScroll(draft.id, event)}
                      rows={8}
                      disabled={busy}
                      className="relative z-10 resize-y bg-transparent text-sm"
                    />
                  </div>
                ) : (
                  <div className="whitespace-pre-wrap text-sm text-foreground line-clamp-5">{draft.body}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div
        ref={mirrorRef}
        aria-hidden="true"
        className="pointer-events-none fixed left-0 top-0 -z-10 whitespace-pre-wrap break-words opacity-0"
      />
      {selection && selectionPosition && (
        <div
          data-selection-toolbar
          ref={toolbarRef}
          className="absolute z-30 flex w-[min(420px,calc(100%_-_16px))] max-w-[calc(100%_-_16px)] items-center gap-2 rounded-md border border-primary/30 bg-background p-2 shadow-lg"
          style={{ left: selectionPosition.left, top: selectionPosition.top }}
        >
          <Input
            value={selectionPrompt}
            onChange={(event) => setSelectionPrompt(event.target.value)}
            placeholder="How should AI rewrite it?"
            className="h-8 min-w-0 flex-1 text-xs"
            disabled={busyDraftId === selection.draftId}
          />
          {selectionNotice && (
            <span className="shrink-0 text-xs text-muted-foreground">{selectionNotice}</span>
          )}
          <Button
            size="sm"
            onClick={() => {
              const draft = drafts.find((item) => item.id === selection.draftId);
              if (draft) void requestSelectionRewrite(draft);
            }}
            disabled={busyDraftId === selection.draftId || !selectionPrompt.trim()}
          >
            {busyDraftId === selection.draftId ? (
              <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Rewriting…</>
            ) : 'Rewrite'}
          </Button>
        </div>
      )}
    </div>
  );
}
