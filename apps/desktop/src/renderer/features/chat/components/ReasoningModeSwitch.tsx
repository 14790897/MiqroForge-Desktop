import React, { useState, useRef, useEffect, useCallback } from 'react';
import { cn } from '../../../lib/utils';

export type ReasoningMode = 'fast' | 'think';

interface ReasoningModeSwitchProps {
  mode: ReasoningMode;
  onChange: (mode: ReasoningMode) => void;
  disabled?: boolean;
}

const ITEMS: { key: ReasoningMode; label: string; desc: string; color: string }[] = [
  { key: 'fast', label: '极速回答', desc: '30 秒内作答', color: '#f59e0b' },
  { key: 'think', label: '深度研究', desc: 'AI 自由发挥', color: '#a855f7' },
];
const LABELS: Record<string, string> = Object.fromEntries(ITEMS.map((i) => [i.key, i.label]));

/** 极速回答 / 深度研究 菜单选择器（issue #680）。
 * 设计 1:1 复用 ExecutionPolicySelector（按钮 + 悬浮菜单 + 快捷键），
 * 只换选项内容与模式色。 */
export const ReasoningModeSwitch: React.FC<ReasoningModeSwitchProps> = ({
  mode,
  onChange,
  disabled,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const cur = ITEMS.find((i) => i.key === mode)!;

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  const pick = useCallback(
    (m: ReasoningMode) => {
      onChange(m);
      setOpen(false);
    },
    [onChange]
  );

  // keyboard: 1/2 direct
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (disabled) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      const m: Record<string, ReasoningMode> = { '1': 'fast', '2': 'think' };
      if (m[e.key]) pick(m[e.key]);
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [pick, disabled]);

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={disabled}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 10px',
          borderRadius: 7,
          fontSize: 11,
          fontWeight: 600,
          cursor: 'pointer',
          border: '1px solid var(--border)',
          background: 'var(--surface)',
          color: 'var(--text)',
          transition: 'all .15s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'var(--surface-muted)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'var(--surface)';
        }}
        aria-label="回答模式"
        aria-expanded={open}
      >
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: cur.color }} />
        <span>{cur.label}</span>
        <span style={{ fontSize: 8, opacity: 0.3 }}>▾</span>
      </button>

      <div
        className={cn(
          'absolute left-0 bottom-full mb-1 z-50 overflow-hidden',
          'transition-all duration-150',
          open
            ? 'opacity-100 scale-100 translate-y-0'
            : 'opacity-0 scale-95 translate-y-1 pointer-events-none'
        )}
        style={{
          minWidth: 220,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          boxShadow: '0 8px 30px rgba(0,0,0,0.12)',
        }}
      >
        <div
          style={{
            padding: '5px 14px 2px',
            fontSize: 10,
            color: 'var(--text-faint)',
            letterSpacing: 0.5,
            textTransform: 'uppercase',
          }}
        >
          回答模式
        </div>
        {ITEMS.map((p) => {
          const active = mode === p.key;
          return (
            <button
              key={p.key}
              type="button"
              onClick={() => pick(p.key)}
              disabled={active}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '6px 14px',
                fontSize: 12,
                cursor: active ? 'default' : 'pointer',
                width: '100%',
                textAlign: 'left',
                transition: 'background .12s',
                color: active ? 'var(--text)' : 'var(--text-muted)',
                fontWeight: active ? 500 : 400,
                background: active ? `${p.color}1A` : 'transparent',
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.background = 'var(--surface-muted)';
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = 'transparent';
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: p.color,
                  opacity: active ? 1 : 0.6,
                  flexShrink: 0,
                }}
              />
              <span style={{ flex: 1 }}>
                <span style={{ display: 'block' }}>{p.label}</span>
                <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>{p.desc}</span>
              </span>
              <span
                style={{
                  fontSize: 10,
                  color: 'var(--text-faint)',
                  border: '1px solid var(--border)',
                  borderRadius: 3,
                  padding: '1px 4px',
                }}
              >
                {['1', '2'][ITEMS.indexOf(p)]}
              </span>
              <span
                style={{
                  fontSize: 10,
                  color: p.color,
                  flexShrink: 0,
                  visibility: active ? 'visible' : 'hidden',
                }}
              >
                ✓
              </span>
            </button>
          );
        })}
        <div style={{ padding: '6px 14px', borderTop: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>极速</span>
            <div
              style={{
                flex: 1,
                height: 4,
                borderRadius: 2,
                background: 'linear-gradient(to right, #f59e0b, #a855f7)',
              }}
            />
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>深度</span>
          </div>
        </div>
      </div>
    </div>
  );
};
