"use client";

import { memo } from "react";
import { FileEdit, FilePlus, FileSearch, FileX, FolderSearch, Wrench } from "lucide-react";

import { ExecutionReportShell } from "@/components/chat/execution-report";
import { cn } from "@/lib/cn";
import type { ToolActivityItem } from "@/lib/chat-stream-runtime";

function toolIcon(label: string) {
  if (label.startsWith("Читаю")) return <FileSearch size={13} aria-hidden />;
  if (label.startsWith("Пишу")) return <FilePlus size={13} aria-hidden />;
  if (label.startsWith("Правлю")) return <FileEdit size={13} aria-hidden />;
  if (label.startsWith("Удаляю")) return <FileX size={13} aria-hidden />;
  if (label.startsWith("Изучаю")) return <FolderSearch size={13} aria-hidden />;
  return <Wrench size={13} aria-hidden />;
}

export const ToolActivityFeed = memo(function ToolActivityFeed({
  items,
}: {
  items: ToolActivityItem[];
}) {
  if (items.length === 0) return null;
  return (
    <ExecutionReportShell>
      <div className="scrollbar-airy max-h-40 space-y-1 overflow-y-auto px-3 py-2">
        {items.map((item) => (
          <div
            key={item.id}
            className={cn(
              "flex items-center gap-2 text-xs",
              item.state === "error"
                ? "text-rose-600"
                : item.state === "done"
                  ? "text-[var(--ar-mist)]"
                  : "text-[var(--ar-black)]"
            )}
          >
            <span className="shrink-0 opacity-70">{toolIcon(item.label)}</span>
            <span className="truncate">{item.label}</span>
          </div>
        ))}
      </div>
    </ExecutionReportShell>
  );
});
