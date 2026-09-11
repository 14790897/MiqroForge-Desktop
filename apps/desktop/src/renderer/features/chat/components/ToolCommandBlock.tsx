import { Check, Copy } from 'lucide-react';

interface ToolCommandBlockProps {
  /** Full untruncated exec command (issue #902). */
  command: string;
  /** Copy-to-clipboard handler owned by the parent (bridge + 2s feedback). */
  onCopy: (text: string) => void;
  /** Whether the copy just succeeded — renders a Check instead of Copy. */
  copied: boolean;
}

/** Expanded exec command shown under a tool-call row: full monospace text
 *  (scrollable/wrappable) with a one-click copy button, distinct from the
 *  output box below it. */
export function ToolCommandBlock({ command, onCopy, copied }: ToolCommandBlockProps) {
  return (
    <div className="mt-1 rounded border border-gray-700 bg-black/80 p-2 font-mono text-[11px] leading-relaxed">
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-[10px] uppercase tracking-wide opacity-70"
          style={{ color: '#9ca3af' }}
        >
          命令
        </span>
        <button
          type="button"
          onClick={() => onCopy(command)}
          title="复制命令"
          aria-label="复制命令"
          data-testid="tool-command-copy"
          data-copied={copied ? 'true' : 'false'}
          className="rounded p-0.5 transition-colors hover:bg-white/10"
        >
          {copied ? (
            <Check size={12} style={{ color: 'var(--success)' }} />
          ) : (
            <Copy size={12} style={{ color: '#d1d5db' }} />
          )}
        </button>
      </div>
      <pre
        className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-all"
        style={{ color: '#d1d5db' }}
      >
        {command}
      </pre>
    </div>
  );
}
