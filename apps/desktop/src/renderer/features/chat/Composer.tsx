import {
  useState,
  useRef,
  useCallback,
  useMemo,
  forwardRef,
  useImperativeHandle,
  type KeyboardEvent,
} from 'react';
import { Textarea } from '../../components/ui/Textarea';
import { ContextMenu, type ContextMenuAction } from '../../components/ContextMenu';
import {
  ExecutionPolicySelector,
  type ExecutionPolicy,
} from '../../components/ExecutionPolicySelector';
import { ReasoningModeSwitch, type ReasoningMode } from './components/ReasoningModeSwitch';
import { Send, Square, Paperclip, Scissors, Copy, ClipboardPaste, CheckCircle } from 'lucide-react';

/**
 * Imperative handle so ChatConsole can drive the composer's text from
 * programmatic paths (edit re-answer / retry prefill, send-failure draft
 * restore, paper-download instruction, session-switch clear) without lifting
 * `input` state back to the top — that lift is what made every keystroke
 * re-render the whole chat tree (#1021).
 */
export interface ComposerHandle {
  setText(text: string): void;
  clear(): void;
  focus(): void;
}

interface ComposerProps {
  streaming: boolean;
  /** `attachments.length > 0` — the composer only needs the boolean. */
  hasAttachments: boolean;
  adjustHint: boolean;
  executionPolicy: ExecutionPolicy;
  onExecutionPolicyChange: (policy: ExecutionPolicy) => void;
  onOpenApprovals?: () => void;
  reasoningMode: ReasoningMode;
  onReasoningModeChange: (mode: ReasoningMode) => void;
  complexHint: boolean;
  onComplexHintDismiss: () => void;
  onAttachClick: () => void;
  onSubmit: (text: string) => void;
  onAbort: () => void;
}

export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  {
    streaming,
    hasAttachments,
    adjustHint,
    executionPolicy,
    onExecutionPolicyChange,
    onOpenApprovals,
    reasoningMode,
    onReasoningModeChange,
    complexHint,
    onComplexHintDismiss,
    onAttachClick,
    onSubmit,
    onAbort,
  },
  ref
) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(
    ref,
    () => ({
      setText: (text: string) => setInput(text),
      clear: () => {
        setInput('');
        // Reset textarea height after sending (mirrors the pre-#1021 reset).
        setTimeout(() => {
          if (textareaRef.current) textareaRef.current.style.height = 'auto';
        }, 0);
      },
      focus: () => textareaRef.current?.focus(),
    }),
    []
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        onSubmit(input);
      }
    },
    [onSubmit, input]
  );

  const inputContextItems = useMemo<ContextMenuAction[]>(
    () => [
      {
        label: '剪切',
        icon: <Scissors size={14} />,
        shortcut: 'Ctrl+X',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          const s = el.selectionStart,
            e = el.selectionEnd;
          if (s === e) return;
          navigator.clipboard.writeText(el.value.slice(s, e)).catch(() => {});
          el.setRangeText('', s, e, 'end');
          // Let React's onChange pick up the new value — manual setInput can
          // drift from the DOM (deleting then requires two passes).
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.focus();
        },
      },
      {
        label: '复制',
        icon: <Copy size={14} />,
        shortcut: 'Ctrl+C',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          const txt = el.value.slice(el.selectionStart, el.selectionEnd);
          if (txt) navigator.clipboard.writeText(txt).catch(() => {});
        },
      },
      {
        label: '粘贴',
        icon: <ClipboardPaste size={14} />,
        shortcut: 'Ctrl+V',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          navigator.clipboard
            .readText()
            .then((text) => {
              if (!text) return;
              // Insert at the caret like native Ctrl+V — replace the current
              // selection range instead of always appending at the end.
              const s = el.selectionStart ?? el.value.length;
              const e = el.selectionEnd ?? s;
              el.setRangeText(text, s, e, 'end');
              // Let React's onChange pick up the new value (single source of
              // truth for state vs DOM — avoids double-delete drift).
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.focus();
            })
            .catch(() => {});
        },
      },
      {
        label: '全选',
        icon: <CheckCircle size={14} />,
        shortcut: 'Ctrl+A',
        divider: true,
        onSelect: () => textareaRef.current?.select(),
      },
    ],
    []
  );

  return (
    <div
      className="flex flex-col rounded-3xl px-7 py-3.5 transition-all"
      data-testid="chat-input-container"
      style={{
        background: 'color-mix(in srgb, var(--surface) 85%, transparent)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: '1px solid color-mix(in srgb, var(--border) 60%, transparent)',
        outline: 'none',
        boxShadow: '0 -4px 20px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04)',
      }}
    >
      {/* Textarea on top — grows up to 1/3 of viewport (DeepSeek style) */}
      <ContextMenu items={inputContextItems} minWidth={160}>
        {({ onContextMenu }) => (
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
            }}
            onKeyDown={handleKeyDown}
            onContextMenu={onContextMenu}
            placeholder={
              adjustHint
                ? '请输入调整要求（例如：市场改为海外、步骤精简到 3 步…）'
                : '请输入消息或拖入文件...'
            }
            rows={1}
            allowResize={true}
            className="w-full border-0 bg-transparent p-0! leading-7! focus:ring-0 focus:border-0 min-h-[52px] max-h-[25vh] text-[15px]"
            style={{ color: 'var(--text)', fieldSizing: 'content' }}
          />
        )}
      </ContextMenu>
      {/* Icon row at the bottom — no text, like DeepSeek */}
      <div className="flex items-center gap-3 pt-1.5 mt-0.5 border-t border-[var(--border-subtle)]">
        <ExecutionPolicySelector
          policy={executionPolicy}
          onChange={onExecutionPolicyChange}
          onOpenApprovals={onOpenApprovals}
        />
        {/* 复杂问题角标（#680 跟进）：轻量气泡挂在模式按钮上，
            3 秒自动消失，不占输入区。 */}
        <div className="relative">
          <ReasoningModeSwitch mode={reasoningMode} onChange={onReasoningModeChange} />
          {complexHint && reasoningMode === 'fast' && (
            <div
              className="absolute left-full ml-2 top-1/2 -translate-y-1/2 z-50 flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] whitespace-nowrap"
              style={{
                background: '#2f2f3a',
                border: '1px solid rgba(157,106,223,.45)',
                color: '#c9a5ef',
                boxShadow: '0 4px 14px rgba(0,0,0,.35)',
              }}
            >
              {/* 指向按钮的小箭头（左侧） */}
              <span
                className="absolute -left-[5px] top-1/2 -translate-y-1/2 w-2 h-2"
                style={{
                  background: '#2f2f3a',
                  borderLeft: '1px solid rgba(157,106,223,.45)',
                  borderBottom: '1px solid rgba(157,106,223,.45)',
                  transform: 'translateY(-50%) rotate(45deg)',
                }}
              />
              <span>💡 建议</span>
              <button
                type="button"
                onClick={() => {
                  onReasoningModeChange('think');
                  onComplexHintDismiss();
                }}
                className="font-semibold cursor-pointer"
                style={{ color: '#d9b8f5' }}
              >
                🧠 深度研究
              </button>
              <button
                type="button"
                onClick={onComplexHintDismiss}
                className="opacity-60 hover:opacity-100 cursor-pointer"
                aria-label="关闭提示"
              >
                ✕
              </button>
            </div>
          )}
        </div>
        {/* AI disclaimer — centered in the mode row, fades when typing */}
        <div className="flex-1 flex items-center justify-center">
          <span
            className="text-size-2xs leading-relaxed tracking-wide text-[var(--text-faint)] italic select-none transition-opacity duration-300"
            style={{ opacity: !input.trim() && !hasAttachments ? 1 : 0 }}
          >
            AI 也会犯错误，对于重要答案请谨慎验证
          </span>
        </div>
        <button
          onClick={onAttachClick}
          className="shrink-0 p-1.5 rounded hover:bg-[var(--surface-muted)] transition-colors"
          title="附件或图片"
          aria-label="附件或图片"
        >
          <Paperclip size={15} style={{ color: 'var(--text-faint)' }} />
        </button>
        {streaming && !input.trim() && !hasAttachments ? (
          <button
            onClick={onAbort}
            title="停止生成"
            aria-label="停止生成"
            className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 hover:bg-[var(--surface-muted)] active:scale-95"
          >
            <Square size={12} style={{ color: 'var(--text-muted)' }} fill="currentColor" />
          </button>
        ) : (
          <button
            onClick={() => onSubmit(input)}
            disabled={!input.trim() && !hasAttachments}
            title={streaming ? '中断当前生成并发送' : '发送'}
            aria-label={streaming ? '中断当前生成并发送' : '发送'}
            className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 hover:brightness-110 hover:-translate-y-px active:scale-95 disabled:opacity-30 disabled:hover:brightness-100 disabled:hover:translate-y-0 disabled:shadow-none"
            style={{
              background:
                'linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 65%, #000))',
              boxShadow: '0 2px 10px color-mix(in srgb, var(--accent) 35%, transparent)',
            }}
          >
            <Send size={14} style={{ color: '#fff' }} />
          </button>
        )}
      </div>
    </div>
  );
});
