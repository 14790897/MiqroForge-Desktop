import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

export type SessionStatus = 'IN-PROGRESS' | 'PENDING' | 'REVIEW' | 'COMPLETED' | 'CC';

export interface StatusDisplayInfo {
  label: string;
  bg: string;
  color: string;
  cardBg: string;
  cardBorder: string;
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
  const { t } = useTranslation();
  const [map, setMap] = useState<Record<string, SessionStatus>>(loadMap);

  const getStatus = useCallback(
    (sessionKey: string): SessionStatus => {
      return map[sessionKey] ?? 'PENDING';
    },
    [map]
  );

  const getStatusDisplay = useCallback(
    (status: SessionStatus): StatusDisplayInfo => {
      switch (status) {
        case 'IN-PROGRESS':
          return {
            label: t('sessionStatus.inProgress'),
            bg: 'var(--tag-inprogress-bg)',
            color: 'var(--tag-inprogress-text)',
            cardBg: 'color-mix(in srgb, var(--surface) 90%, var(--tag-inprogress-bg))',
            cardBorder: 'color-mix(in srgb, var(--border-subtle) 70%, var(--tag-inprogress-bg))',
          };
        case 'REVIEW':
          return {
            label: t('sessionStatus.review'),
            bg: 'var(--tag-review-bg)',
            color: 'var(--tag-review-text)',
            cardBg: 'color-mix(in srgb, var(--surface) 75%, var(--tag-review-bg))',
            cardBorder: 'color-mix(in srgb, var(--border-subtle) 70%, var(--tag-review-text))',
          };
        case 'COMPLETED':
          return {
            label: t('sessionStatus.completed'),
            bg: 'var(--tag-completed-bg)',
            color: 'var(--tag-completed-text)',
            cardBg: 'color-mix(in srgb, var(--surface) 75%, var(--tag-completed-bg))',
            cardBorder: 'color-mix(in srgb, var(--border-subtle) 70%, var(--tag-completed-text))',
          };
        case 'CC':
          return {
            label: t('sessionStatus.cc'),
            bg: 'var(--tag-cc-bg)',
            color: 'var(--tag-cc-text)',
            cardBg: 'color-mix(in srgb, var(--surface) 75%, var(--tag-cc-bg))',
            cardBorder: 'color-mix(in srgb, var(--border-subtle) 70%, var(--tag-cc-text))',
          };
        case 'PENDING':
        default:
          return {
            label: t('sessionStatus.pending'),
            bg: 'var(--surface-muted)',
            color: 'var(--text-faint)',
            cardBg: 'var(--surface)',
            cardBorder: 'var(--border-subtle)',
          };
      }
    },
    [t]
  );

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
