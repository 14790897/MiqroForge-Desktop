import { useState, useEffect, useCallback, useRef, type MouseEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export interface ContextMenuAction {
  label: string;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  divider?: boolean;
  icon?: ReactNode;
  onSelect: () => void;
  /** Called when mouse enters this item (for preview/highlight) */
  onEnter?: () => void;
  /** Called when mouse leaves this item */
  onLeave?: () => void;
}

interface Position {
  x: number;
  y: number;
}

interface Props {
  /** Render prop: pass the onContextMenu handler to your element */
  children: (props: { onContextMenu: (e: MouseEvent) => void }) => ReactNode;
  /** Menu items */
  items: ContextMenuAction[];
  /** Optional min width (default 180) */
  minWidth?: number;
}

export function ContextMenu({ children, items, minWidth = 180 }: Props) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<Position>({ x: 0, y: 0 });
  const menuRef = useRef<HTMLDivElement>(null);
  const [adjustedPos, setAdjustedPos] = useState<Position>({ x: 0, y: 0 });
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const onContextMenu = useCallback((e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setPosition({ x: e.clientX, y: e.clientY });
    setOpen(true);
  }, []);

  const close = useCallback(() => {
    // Every dismissal path (Escape, outside click, item select) must clear
    // any pending hover preview, not just onMouseDown.
    if (hoveredIndex !== null) items[hoveredIndex]?.onLeave?.();
    setHoveredIndex(null);
    setOpen(false);
  }, [hoveredIndex, items]);

  // Adjust position after render to avoid screen edges
  useEffect(() => {
    if (!open || !menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const { innerWidth, innerHeight } = window;
    let x = position.x;
    let y = position.y;

    if (x + rect.width > innerWidth - 8) {
      x = innerWidth - rect.width - 8;
    }
    if (y + rect.height > innerHeight - 8) {
      y = innerHeight - rect.height - 8;
    }
    if (x < 4) x = 4;
    if (y < 4) y = 4;

    setAdjustedPos({ x, y });
  }, [open, position]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, close]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: Event) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        close();
      }
    };
    const id = setTimeout(() => {
      document.addEventListener('click', handler);
      document.addEventListener('contextmenu', handler);
    }, 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', handler);
      document.removeEventListener('contextmenu', handler);
    };
  }, [open, close]);

  return (
    <>
      {children({ onContextMenu })}
      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-50"
            onClick={close}
            onContextMenu={(e) => {
              e.preventDefault();
              close();
            }}
          >
            <div
              ref={menuRef}
              className="absolute bg-[var(--surface-elevated)] border border-[var(--border)] rounded-lg shadow-lg py-1 overflow-hidden"
              style={{ left: adjustedPos.x, top: adjustedPos.y, minWidth }}
              onClick={(e) => e.stopPropagation()}
            >
              {items.map((item, i) => (
                <div key={i}>
                  {item.divider && <div className="my-1 border-t border-[var(--border-subtle)]" />}
                  <button
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      item.onSelect();
                      close(); // close() invokes onLeave for the hovered item
                    }}
                    onMouseEnter={() => {
                      setHoveredIndex(i);
                      item.onEnter?.();
                    }}
                    onMouseLeave={() => {
                      setHoveredIndex(null);
                      item.onLeave?.();
                    }}
                    disabled={item.disabled}
                    className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center justify-between gap-4 ${
                      item.disabled
                        ? 'text-[var(--text-faint)] cursor-not-allowed'
                        : item.danger
                          ? 'text-[var(--danger)] hover:bg-red-50 dark:hover:bg-red-900/20'
                          : 'text-[var(--text)] hover:bg-[var(--surface-muted)]'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      {item.icon && (
                        <span className="w-4 h-4 flex items-center justify-center shrink-0">
                          {item.icon}
                        </span>
                      )}
                      {item.label}
                    </span>
                    {item.shortcut && (
                      <span className="text-[var(--text-faint)] shrink-0">{item.shortcut}</span>
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>,
          document.body
        )}
    </>
  );
}
