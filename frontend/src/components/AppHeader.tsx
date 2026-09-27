'use client';

import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { MemoryManager } from '@/components/MemoryManager';
import {
  APP_HEADER_SEARCH_INPUT_PROPS,
  APP_HEADER_SEARCH_PLACEHOLDER,
  getAskAiPrompt,
} from '@/lib/app-header-contract.mjs';
import { RefreshCw, Search, Sparkles, Wrench } from 'lucide-react';

interface AppHeaderProps {
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
  onAskAi: (prompt: string) => void;
  onSync: () => void;
  isSyncing: boolean;
  onReloadLocalData: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
}

export function AppHeader({
  searchQuery,
  onSearchQueryChange,
  onAskAi,
  onSync,
  isSyncing,
  onReloadLocalData,
  isLoading,
  isRefreshing,
}: AppHeaderProps) {
  return (
    <header className="shrink-0 rounded-xl border border-border bg-card px-4 py-2 shadow-sm">
      <div className="flex w-full items-center gap-3">
        <h1 className="shrink-0 text-2xl font-bold tracking-tight text-foreground">FeedFlux</h1>

        <div className="relative min-w-0 flex-1 rounded-full shadow-sm">
          <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
            <Search className="h-4 w-4 text-muted-foreground" />
          </div>
          <Input
            type="text"
            {...APP_HEADER_SEARCH_INPUT_PROPS}
            placeholder={APP_HEADER_SEARCH_PLACEHOLDER}
            className="w-full rounded-full border-0 bg-muted/50 py-2 pl-11 pr-4 text-sm shadow-none focus-visible:ring-1"
            value={searchQuery}
            onChange={(event) => onSearchQueryChange(event.target.value)}
          />
        </div>

        <Button
          variant="outline"
          className="shrink-0 whitespace-nowrap px-4"
          onClick={() => onAskAi(getAskAiPrompt(searchQuery))}
        >
          <Sparkles className="mr-1.5 h-4 w-4" />
          Ask AI
        </Button>
        <MemoryManager />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
              title="Tools"
              aria-label="Tools"
            >
              <Wrench className="h-4 w-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onSync} disabled={isSyncing}>
              <RefreshCw className="h-4 w-4" />
              {isSyncing ? 'Syncing...' : 'Sync Outlook'}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onReloadLocalData} disabled={isLoading || isRefreshing || isSyncing}>
              <RefreshCw className="h-4 w-4" />
              {isLoading || isRefreshing ? 'Updating...' : 'Reload Local Data'}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
