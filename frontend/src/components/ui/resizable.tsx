"use client"

import { GripVertical } from "lucide-react"
import * as ResizablePrimitive from "react-resizable-panels"

import { cn } from "@/lib/utils"

const ResizablePanelGroup = ({
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.Group>) => (
  <ResizablePrimitive.Group
    className={cn(
      "flex h-full w-full data-[panel-group-orientation=vertical]:flex-col",
      className
    )}
    {...props}
  />
)

const ResizablePanel = ResizablePrimitive.Panel

const ResizableHandle = ({
  withHandle,
  className,
  children,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.Separator> & {
  withHandle?: boolean
}) => (
  <ResizablePrimitive.Separator
    className={cn(
      "relative flex w-px items-center justify-center bg-slate-200 dark:bg-slate-800 after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-slate-950 focus-visible:ring-offset-1 data-[panel-group-orientation=vertical]:h-px data-[panel-group-orientation=vertical]:w-full data-[panel-group-orientation=vertical]:after:left-0 data-[panel-group-orientation=vertical]:after:h-1 data-[panel-group-orientation=vertical]:after:w-full data-[panel-group-orientation=vertical]:after:-translate-y-1/2 data-[panel-group-orientation=vertical]:after:translate-x-0 dark:focus-visible:ring-slate-300",
      className
    )}
    {...props}
  >
    {withHandle && (
      <div className="z-10 flex h-4 w-3 items-center justify-center rounded-sm border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <GripVertical className="h-2.5 w-2.5" />
      </div>
    )}
    {children}
  </ResizablePrimitive.Separator>
)

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
