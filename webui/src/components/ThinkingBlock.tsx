import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

interface ThinkingBlockProps {
  reasoning: string;
  isThinking?: boolean;
}

export function ThinkingBlock({ reasoning, isThinking = false }: ThinkingBlockProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="mb-3 rounded-lg border border-violet-200/40 bg-violet-50/30 dark:border-violet-800/30 dark:bg-violet-950/20">
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left"
      >
        {collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-violet-400" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-violet-400" />
        )}
        <span className="text-[12px] font-medium text-violet-500 dark:text-violet-400">
          🧠 深度思考
        </span>
        {isThinking && (
          <span className="ml-1 inline-flex gap-0.5">
            <span className="h-1 w-1 animate-bounce rounded-full bg-violet-400 [animation-delay:0ms]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-violet-400 [animation-delay:150ms]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-violet-400 [animation-delay:300ms]" />
          </span>
        )}
      </button>
      {!collapsed && (
        <div className="border-t border-violet-200/30 px-3 py-2 dark:border-violet-800/20">
          <pre
            className={cn(
              "max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] break-words font-mono text-[11px] leading-relaxed text-muted-foreground",
              isThinking && "after:ml-0.5 after:animate-pulse after:content-['▋']",
            )}
          >
            {reasoning}
          </pre>
        </div>
      )}
    </div>
  );
}
