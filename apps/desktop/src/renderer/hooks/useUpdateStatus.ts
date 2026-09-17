import { useEffect, useState } from 'react';
import type { UpdateSnapshot } from '../../shared/ipc';

/**
 * 自动更新状态（#1124）。
 *
 * 复用 useQraftStatus 的「事件订阅 + 首帧快照」模式：先 invoke 取快照，
 * 之后由主进程 update:changed 事件推送覆盖（gotEvent 后不再被过期快照覆盖）。
 */
export function useUpdateStatus() {
  const [status, setStatus] = useState<UpdateSnapshot | null>(null);

  useEffect(() => {
    let alive = true;
    let gotEvent = false;
    let unsubscribe: (() => void) | undefined;

    try {
      window.miqi.update
        .status()
        .then((s) => {
          if (alive && !gotEvent) setStatus(s);
        })
        .catch(() => {
          /* IPC 未就绪时保持空状态 */
        });
      unsubscribe = window.miqi.update.onStatusChanged((next) => {
        gotEvent = true;
        setStatus(next);
      });
    } catch {
      /* 旧版 preload（如 smoke mock）可能没有 update 命名空间 */
    }

    return () => {
      alive = false;
      unsubscribe?.();
    };
  }, []);

  return status;
}
