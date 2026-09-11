import { useState, useRef, useCallback, useEffect } from 'react';

export interface UsePanelResizeOptions {
  minWidth: number;
  maxWidth: number;
  defaultWidth: number;
  /** Compute new width from mouse position and container rect.
   *  For left-mounted panels: `e.clientX - rect.left`
   *  For right-mounted panels: `window.innerWidth - e.clientX` */
  computeWidth: (e: MouseEvent, rect: DOMRect) => number;
}

export function usePanelResize(options: UsePanelResizeOptions) {
  const { minWidth, maxWidth, defaultWidth, computeWidth } = options;
  const [width, setWidth] = useState(defaultWidth);
  const isResizing = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Keep the latest options in refs so the document listeners attach ONCE.
  // Depending on computeWidth directly (#977): callers pass an inline arrow
  // (new identity every render), so each setWidth re-render re-ran this
  // effect — its cleanup resets isResizing mid-drag, and every mousemove
  // after the first one was ignored. Only the first step of a drag applied.
  const latestRef = useRef({ minWidth, maxWidth, computeWidth });
  latestRef.current = { minWidth, maxWidth, computeWidth };

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const { minWidth: min, maxWidth: max, computeWidth: compute } = latestRef.current;
      const newWidth = compute(e, rect);
      setWidth(Math.max(min, Math.min(max, newWidth)));
    };
    const handleMouseUp = () => {
      if (isResizing.current) {
        isResizing.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      // cleanup if unmounted during drag
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, []);

  return { width, containerRef, handleMouseDown };
}
