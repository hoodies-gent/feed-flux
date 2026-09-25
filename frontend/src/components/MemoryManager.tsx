'use client';

import { useCallback, useEffect, useState } from 'react';
import { Brain, Eye, EyeOff, Loader2, Pencil, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import {
  clearSemanticMemories,
  deleteSemanticMemory,
  listSemanticMemories,
  setSemanticMemoryEnabled,
  updateSemanticMemory,
  type SemanticMemory,
} from '@/lib/api';


function provenance(memory: SemanticMemory): string {
  const source = memory.source.replaceAll('_', ' ');
  const date = new Date(memory.confirmed_at * 1000).toLocaleDateString();
  return `${source}${memory.source_ref ? ` · ${memory.source_ref}` : ''} · ${date}`;
}


export function MemoryManager() {
  const [open, setOpen] = useState(false);
  const [memories, setMemories] = useState<SemanticMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | 'all' | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editedValue, setEditedValue] = useState('');

  const loadMemories = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMemories(await listSemanticMemories());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load memories');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void loadMemories();
  }, [open, loadMemories]);

  const mutate = async (
    target: number | 'all',
    action: () => Promise<unknown>,
    successMessage: string,
  ): Promise<boolean> => {
    setBusyId(target);
    setError(null);
    try {
      await action();
      await loadMemories();
      toast.success(successMessage);
      return true;
    } catch (mutationError) {
      const message = mutationError instanceof Error
        ? mutationError.message
        : 'Memory update failed';
      setError(message);
      toast.error(message);
      return false;
    } finally {
      setBusyId(null);
    }
  };

  const startEditing = (memory: SemanticMemory) => {
    setEditingId(memory.id);
    setEditedValue(memory.value);
  };

  const saveEdit = async (memory: SemanticMemory) => {
    const value = editedValue.trim();
    if (!value || value === memory.value) {
      setEditingId(null);
      return;
    }
    const saved = await mutate(
      memory.id,
      () => updateSemanticMemory(memory.id, value),
      'Memory updated',
    );
    if (saved) setEditingId(null);
  };

  const removeMemory = async (memory: SemanticMemory) => {
    const confirmed = window.confirm(
      `Permanently forget “${memory.key}” and scrub its history?`,
    );
    if (!confirmed) return;
    await mutate(
      memory.id,
      () => deleteSemanticMemory(memory.id),
      'Memory forgotten',
    );
  };

  const clearAll = async () => {
    const confirmed = window.confirm(
      'Permanently forget every memory and scrub their histories?',
    );
    if (!confirmed) return;
    const cleared = await mutate('all', clearSemanticMemories, 'All memories forgotten');
    if (cleared) setEditingId(null);
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm" className="shrink-0 text-muted-foreground hover:text-foreground">
          <Brain className="h-4 w-4" />
          Memory
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        <SheetHeader className="border-b pr-12">
          <SheetTitle className="flex items-center gap-2">
            <Brain className="h-4 w-4" />
            Memory
          </SheetTitle>
          <SheetDescription>
            Confirmed preferences and facts used across conversations. Disabled memories are never recalled.
          </SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && (
            <div role="alert" className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {loading && memories.length === 0 ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading memories…
            </div>
          ) : memories.length === 0 ? (
            <div className="flex h-40 flex-col items-center justify-center rounded-lg border border-dashed text-center">
              <Brain className="mb-3 h-7 w-7 text-muted-foreground" />
              <p className="text-sm font-medium">No confirmed memories</p>
              <p className="mt-1 max-w-xs text-xs text-muted-foreground">
                Ask the agent to remember a stable preference or fact and it will appear here.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {memories.map((memory) => {
                const editing = editingId === memory.id;
                const busy = busyId === memory.id || busyId === 'all';
                const controlsDisabled = busyId !== null;
                return (
                  <section
                    key={memory.id}
                    className={`rounded-lg border p-3 ${memory.status === 'disabled' ? 'bg-muted/40 opacity-75' : 'bg-card'}`}
                  >
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <h3 className="break-words text-sm font-medium">{memory.key}</h3>
                          <Badge variant={memory.status === 'active' ? 'secondary' : 'outline'}>
                            {memory.status}
                          </Badge>
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          <Badge variant="outline">{memory.memory_type}</Badge>
                          <Badge variant="outline">{memory.workflow_scope}</Badge>
                          <Badge variant="outline">{memory.contact_scope ?? 'all contacts'}</Badge>
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          title="Edit memory"
                          disabled={controlsDisabled}
                          onClick={() => startEditing(memory)}
                        >
                          <Pencil />
                          <span className="sr-only">Edit memory</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          title={memory.status === 'active' ? 'Disable memory' : 'Enable memory'}
                          disabled={controlsDisabled}
                          onClick={() => void mutate(
                            memory.id,
                            () => setSemanticMemoryEnabled(memory.id, memory.status !== 'active'),
                            memory.status === 'active' ? 'Memory disabled' : 'Memory enabled',
                          )}
                        >
                          {busy ? <Loader2 className="animate-spin" /> : memory.status === 'active' ? <EyeOff /> : <Eye />}
                          <span className="sr-only">
                            {memory.status === 'active' ? 'Disable memory' : 'Enable memory'}
                          </span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="text-destructive hover:text-destructive"
                          title="Forget memory"
                          disabled={controlsDisabled}
                          onClick={() => void removeMemory(memory)}
                        >
                          <Trash2 />
                          <span className="sr-only">Forget memory</span>
                        </Button>
                      </div>
                    </div>

                    {editing ? (
                      <div className="mt-3 space-y-2">
                        <Textarea
                          autoFocus
                          value={editedValue}
                          onChange={(event) => setEditedValue(event.target.value)}
                          disabled={controlsDisabled}
                          className="min-h-24 resize-y"
                        />
                        <div className="flex justify-end gap-2">
                          <Button variant="outline" size="sm" disabled={controlsDisabled} onClick={() => setEditingId(null)}>
                            Cancel
                          </Button>
                          <Button size="sm" disabled={controlsDisabled || !editedValue.trim()} onClick={() => void saveEdit(memory)}>
                            {busy && <Loader2 className="animate-spin" />}
                            Save
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed">
                        {memory.value}
                      </p>
                    )}

                    <p className="mt-3 truncate text-[11px] text-muted-foreground" title={provenance(memory)}>
                      v{memory.version} · {provenance(memory)}
                    </p>
                  </section>
                );
              })}
            </div>
          )}
        </div>

        {memories.length > 0 && (
          <div className="border-t p-4">
            <Button
              variant="outline"
              size="sm"
              className="w-full text-destructive hover:text-destructive"
              disabled={busyId !== null}
              onClick={() => void clearAll()}
            >
              {busyId === 'all' ? <Loader2 className="animate-spin" /> : <Trash2 />}
              Forget all memories
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
