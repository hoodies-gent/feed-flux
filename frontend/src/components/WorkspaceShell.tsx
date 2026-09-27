'use client';

import type { ReactNode } from 'react';
import type { Layout } from 'react-resizable-panels';

import { ResizablePanelGroup } from '@/components/ui/resizable';
import { WORKSPACE_PANEL_GAP_CLASS } from '@/lib/workspace-chrome-contract.mjs';

interface WorkspaceShellProps {
  header: ReactNode;
  collapsedAssistant: ReactNode;
  children: ReactNode;
  isAgentOpen: boolean;
  layout: Layout;
  onLayoutChanged: (layout: Layout) => void;
}

export function WorkspaceShell({
  header,
  collapsedAssistant,
  children,
  isAgentOpen,
  layout,
  onLayoutChanged,
}: WorkspaceShellProps) {
  return (
    <div className="h-screen overflow-hidden bg-background p-1.5 font-[family-name:var(--font-geist-sans)]">
      <div className="mx-auto flex h-full w-full max-w-[1800px] flex-col gap-1.5">
        {header}
        <div className={`flex min-h-0 flex-1 ${WORKSPACE_PANEL_GAP_CLASS}`}>
          <ResizablePanelGroup
            key={isAgentOpen ? 'agent-open' : 'agent-closed'}
            id="mail-layout-group"
            orientation="horizontal"
            resizeTargetMinimumSize={{ coarse: 20, fine: 20 }}
            className="min-h-0 min-w-0 flex-1"
            defaultLayout={layout}
            onLayoutChanged={onLayoutChanged}
          >
            {children}
          </ResizablePanelGroup>
          {collapsedAssistant}
        </div>
      </div>
    </div>
  );
}
