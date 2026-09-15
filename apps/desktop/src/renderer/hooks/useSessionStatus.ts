import { useState, useCallback } from 'react';

export type SessionStatus = 'IN-PROGRESS' | 'PENDING' | 'REVIEW' | 'COMPLETED' | 'CC';

export interface StatusDisplayInfo {
  label: string;
  /** 列表卡片状态点的实心填充色。 */
  dot: string;
}

const STORAGE_KEY = 'miqi:sessionStatuses';

function loadMap(): Record<string, SessionStatus> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveMap(map: Record<string, SessionStatus>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* localStorage unavailable — silently ignore */
  }
}

export function useSessionStatus() {
  const [map, setMap] = useState<Record<string, SessionStatus>>(loadMap);

  const getStatus = useCallback(
    (sessionKey: string): SessionStatus => {
      return map[sessionKey] ?? 'PENDING';
    },
    [map]
  );

  const getStatusDisplay = useCallback((status: SessionStatus): StatusDisplayInfo => {
    switch (status) {
      case 'IN-PROGRESS':
        return { label: '进行中', dot: 'var(--tag-inprogress-bg)' };
      case 'REVIEW':
        return { label: '待审阅', dot: 'var(--tag-review-text)' };
      case 'COMPLETED':
        // 用 success 绿而不是 --tag-completed-text：后者(#1d6fd8)与进行中的
        // #3b82f6 同为蓝，缩到 8px 的状态点上肉眼分不出。
        return { label: '已完成', dot: 'var(--success)' };
      case 'CC':
        return { label: '抄送 (旧)', dot: 'var(--tag-cc-text)' };
      case 'PENDING':
      default:
        return { label: '待处理', dot: 'var(--text-faint)' };
    }
  }, []);

  const setStatus = useCallback((sessionKey: string, status: SessionStatus) => {
    setMap((prev) => {
      const next = { ...prev, [sessionKey]: status };
      saveMap(next);
      return next;
    });
  }, []);

  const clearStatus = useCallback((sessionKey: string) => {
    setMap((prev) => {
      const next = { ...prev };
      delete next[sessionKey];
      saveMap(next);
      return next;
    });
  }, []);

  return { getStatus, getStatusDisplay, setStatus, clearStatus };
}
