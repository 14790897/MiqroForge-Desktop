import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../lib/utils';

export type ExecutionPolicy = 'plan' | 'manual' | 'edit' | 'auto';

type P = { key: ExecutionPolicy; label: string; desc: string; color: string };
const ITEMS: P[] = [
  { key: 'plan', label: '规划', desc: '只分析出方案，不动手', color: '#a855f7' },
  { key: 'manual', label: '手动', desc: '每步说明并等待确认', color: '#0b7f91' },
  { key: 'edit', label: '允许编辑', desc: '改文件自动放行，危险操作确认', color: '#3b82f6' },
  { key: 'auto', label: '自动', desc: '完全自主执行，无需确认', color: '#f59e0b' },
];
const LABELS: Record<string, string> = Object.fromEntries(ITEMS.map((p) => [p.key, p.label]));

interface Props {
  policy: ExecutionPolicy;
  onChange: (p: ExecutionPolicy) => void;
  disabled?: boolean;
  onOpenApprovals?: () => void;
}

export function ExecutionPolicySelector({ policy, onChange, disabled, onOpenApprovals }: Props) {
  const [open, setOpen] = useState(false);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const t = useRef(0);
  const cur = ITEMS.find((i) => i.key === policy)!;

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  const toastFn = useCallback((msg: string) => {
    setToast(msg);
    if (t.current) clearTimeout(t.current);
    t.current = window.setTimeout(() => setToast(null), 2000);
  }, []);
  useEffect(() => () => clearTimeout(t.current), []);

  const pick = useCallback(
    (p: ExecutionPolicy) => {
      if (p === 'auto') {
        setConfirmAuto(true);
        setOpen(false);
        return;
      }
      onChange(p);
      setOpen(false);
      toastFn(`✓ ${LABELS[p]} 已启用`);
    },
    [onChange, toastFn]
  );

  // Sync auto mode to sessionStorage so TopBar/ApprovalBypassBanner can react
  useEffect(() => {
    if (policy === 'auto') sessionStorage.setItem('miqi:mode:auto', '1');
    else sessionStorage.removeItem('miqi:mode:auto');
    window.dispatchEvent(new Event('miqi:mode-changed'));
  }, [policy]);

  // keyboard: 1/2/3/4 direct, Shift+Tab cycle
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (disabled) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      const m: Record<string, ExecutionPolicy> = {
        '1': 'plan',
        '2': 'manual',
        '3': 'edit',
        '4': 'auto',
      };
      if (m[e.key]) {
        pick(m[e.key]);
        return;
      }
      if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault();
        const order: ExecutionPolicy[] = ['plan', 'manual', 'edit', 'auto'];
        const idx = order.indexOf(policy);
        const next = order[(idx + 1) % order.length];
        pick(next);
      }
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [pick, disabled, policy]);

  return (
    <>
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
            border: `1px solid ${policy === 'auto' ? cur.color : 'var(--border)'}`,
            background: policy === 'auto' ? `${cur.color}14` : 'var(--surface)',
            color: policy === 'auto' ? cur.color : 'var(--text)',
            transition: 'all .15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background =
              policy === 'auto' ? `${cur.color}22` : 'var(--surface-muted)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background =
              policy === 'auto' ? `${cur.color}14` : 'var(--surface)';
          }}
        >
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: cur.color }} />
          <span>{cur.label}</span>
          <span style={{ fontSize: 8, opacity: 0.3 }}>▾</span>
          {cur.key === 'auto' && <span style={{ fontSize: 13 }}>⚠</span>}
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
            minWidth: 240,
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
            Agent 模式
          </div>
          {ITEMS.map((p) => {
            const active = policy === p.key;
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
                  {['1', '2', '3', '4'][ITEMS.indexOf(p)]}
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
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>保守</span>
              <div
                style={{
                  flex: 1,
                  height: 4,
                  borderRadius: 2,
                  background: 'linear-gradient(to right, #a855f7, #0b7f91, #3b82f6, #f59e0b)',
                }}
              />
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>自动</span>
            </div>
            {onOpenApprovals && (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onOpenApprovals();
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  width: '100%',
                  marginTop: 4,
                  padding: '3px 0',
                  fontSize: 11,
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  border: 'none',
                  background: 'transparent',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text)')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-muted)')}
              >
                <span style={{ fontSize: 12 }}>⚙</span>
                <span>审批设置</span>
                <span style={{ fontSize: 10, color: 'var(--text-faint)', marginLeft: 'auto' }}>
                  →
                </span>
              </button>
            )}
          </div>
        </div>
      </div>

      {confirmAuto &&
        createPortal(
          <div
            onClick={() => setConfirmAuto(false)}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 200,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(0,0,0,0.35)',
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 18,
                padding: '12px 18px',
                maxWidth: 300,
                width: '90%',
                boxShadow: '0 16px 48px rgba(0,0,0,0.16)',
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                开启自动模式
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '3px 0 0' }}>
                Agent 将完全自主执行，不再弹窗确认
              </p>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 10 }}>
                <button
                  onClick={() => setConfirmAuto(false)}
                  style={{
                    padding: '5px 14px',
                    borderRadius: 8,
                    fontSize: 11,
                    fontWeight: 500,
                    cursor: 'pointer',
                    border: '1px solid var(--border)',
                    background: 'transparent',
                    color: 'var(--text-muted)',
                  }}
                >
                  取消
                </button>
                <button
                  onClick={() => {
                    onChange('auto');
                    setConfirmAuto(false);
                    toastFn('✓ 自主 已启用');
                  }}
                  style={{
                    padding: '5px 14px',
                    borderRadius: 8,
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: 'pointer',
                    border: 'none',
                    background: 'var(--warning)',
                    color: '#fff',
                  }}
                >
                  确认
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {createPortal(
        <div
          className={cn(
            'fixed bottom-24 left-1/2 -translate-x-1/2 z-[150] px-4 py-2 rounded-full text-size-2xs font-medium whitespace-nowrap transition-all duration-200',
            toast ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2 pointer-events-none'
          )}
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            boxShadow: '0 4px 20px rgba(0,0,0,0.12)',
            color: 'var(--text)',
          }}
        >
          {toast}
        </div>,
        document.body
      )}
    </>
  );
}
