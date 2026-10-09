/**
 * 主进程侧的在途聊天回合登记（#1257）。
 *
 * 只在 `chat.send` 的转发处登记、在终态事件（final / error / aborted）或通道
 * 异常时摘除。用途只有一个：**登录态结束时中断在途回合**。
 *
 * 为什么需要它：平台判定登录失效后应用会自动退出登录（清 token 文件），
 * 但后端那个回合不会因此停下 —— 它既不返回也不报错，界面上一直显示「生成中」，
 * 会话还被 turn lock 挡着（新消息一律 TURN_IN_PROGRESS，直到超时）。
 * 主进程是唯一知道「哪些会话还有在途回合」且不随登录门卸载的地方，所以在
 * 这里登记，登出时逐个 `chat.abort`。
 */

const inFlight = new Set<string>();

/** 登记一个在途回合（同一会话多条消息只记一次，中断按会话进行）。 */
export function trackInFlightChat(sessionKey: string): void {
  if (sessionKey) inFlight.add(sessionKey);
}

/** 回合到达终态（含被中断）后摘除登记。 */
export function untrackInFlightChat(sessionKey: string): void {
  inFlight.delete(sessionKey);
}

/** 当前仍登记着在途回合的会话键（拷贝，调用方可安全遍历）。 */
export function inFlightChatSessions(): string[] {
  return [...inFlight];
}

/** 清空登记（登出中断后再调用，避免残留的登记让下一次登出打断新会话）。 */
export function clearInFlightChats(): void {
  inFlight.clear();
}

/** 发中断请求所需的最小 bridge 接口（BridgeManager 结构上即满足，便于单测）。 */
export interface ChatAbortSender {
  sendSafeWithError(
    method: string,
    params?: Record<string, unknown>
  ): Promise<{ ok: true; value: unknown } | { ok: false; error: string; code?: string }>;
}

/**
 * 中断登记表里的每个在途会话，返回被中断的会话键。
 *
 * 先清空登记再逐条发中断：中断失败（bridge 未起 / 刚热重启）不该让残留登记
 * 在**下一次**登出时误伤新会话；单个会话失败也不影响其余会话。
 */
export async function abortInFlightChats(sender: ChatAbortSender): Promise<string[]> {
  const keys = inFlightChatSessions();
  clearInFlightChats();
  for (const sessionKey of keys) {
    const res = await sender.sendSafeWithError('chat.abort', { session_key: sessionKey });
    if (!res.ok) {
      console.warn(`[chat] 中断在途回合失败（${sessionKey}）：${res.error}`);
    }
  }
  return keys;
}
