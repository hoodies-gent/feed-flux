'use client';

import type { ReactNode } from 'react';
import type { Layout } from 'react-resizable-panels';

import { ResizablePanelGroup } from '@/components/ui/resizable';

interface WorkspaceShellProps {
  header: ReactNode;
  children: ReactNode;
  isAgentOpen: boolean;
  layout: Layout;
  onLayoutChanged: (layout: Layout) => void;
}

export function WorkspaceShell({
  header,
  children,
  isAgentOpen,
  layout,
  onLayoutChanged,
}: WorkspaceShellProps) {
  return (
    <div className="h-screen overflow-hidden bg-background px-4 py-2 font-[family-name:var(--font-geist-sans)]">
      <div className="mx-auto flex h-full w-full max-w-[1800px] flex-col gap-3">
        {header}
        <ResizablePanelGroup
          key={isAgentOpen ? 'agent-open' : 'agent-closed'}
          id="mail-layout-group"
          orientation="horizontal"
          resizeTargetMinimumSize={{ coarse: 20, fine: 20 }}
          className="min-h-0 flex-1"
          defaultLayout={layout}
          onLayoutChanged={onLayoutChanged}
        >
          {children}
        </ResizablePanelGroup>
      </div>
    </div>
  );
}
