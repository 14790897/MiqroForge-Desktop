import { useEffect, useState } from 'react';
import type { QraftStatus } from '../../shared/ipc';

/**
 * 共享 Qraft 登录态（#835 登录门控；#922 网关门控）。
 *
 * 复用 QraftPage 的 status + onStatusChanged 模式，供设置页 / Providers 页
 * 判断「是否已登录」以及「AI 网关是否 active」，在未登录或登录但网关未就绪
 * 时禁用模型选择并引导。
 *
 * 返回值语义（#922）：
 *  - loggedIn：已登录（无论网关状态）。
 *  - aiGatewayStatus：登录态下平台下发的网关状态；未登录/未下发为 undefined。
 *  - gatewayActive：已登录且 aiGatewayStatus === 'active' → 允许模型走网关。
 *  - aiGatewayKnown：登录且平台明确返回了 aiGateway（用于区分"未下发"与"非 active"）。
 */
export function useQraftStatus() {
  const [status, setStatus] = useState<QraftStatus | null>(null);

  useEffect(() => {
    let alive = true;
    let gotEvent = false; // 事件已到 → 初始快照视为过期，避免覆盖更新的状态
    let unsubscribe: (() => void) | undefined;

    try {
      window.miqi.qraft
        .status()
        .then((s) => {
          if (alive && !gotEvent) setStatus(s);
        })
        .catch(() => {
          /* IPC 未就绪时保持空状态 */
        });
      unsubscribe = window.miqi.qraft.onStatusChanged((next) => {
        gotEvent = true;
        setStatus(next);
      });
    } catch {
      /* 旧版 preload（如 smoke mock）可能没有 qraft 命名空间 */
    }

    return () => {
      alive = false;
      unsubscribe?.();
    };
  }, []);

  const loggedIn = status?.loggedIn === true;
  const aiGatewayStatus = loggedIn ? status?.aiGateway?.status : undefined;
  const gatewayActive = aiGatewayStatus === 'active';
  const aiGatewayKnown = loggedIn && aiGatewayStatus !== undefined;
  return { status, loggedIn, aiGatewayStatus, gatewayActive, aiGatewayKnown };
}
