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
 *
 * 登记粒度是**每个请求**而不是会话：同一会话可以同时有两个在途请求（后到的
 * 会被后端以 TURN_IN_PROGRESS 拒掉），若按会话记一个条目，被拒那次一收到终态
 * 就会把先到那个仍在跑的回合一并摘掉，登出时漏掉它（CodeRabbit #1260）。
 */

let nextRequestId = 0;
/** requestId → 会话键。 */
const inFlight = new Map<number, string>();

/**
 * 登记一次在途请求，返回它的注销句柄（幂等）。
 *
 * 用句柄而不是 `(sessionKey, requestId)` 两个参数：调用方拿不到、也不需要
 * 拼 id，终结时调一次即可；重复调用（多个终态事件 + 通道异常）不会误摘别人。
 */
export function trackInFlightChat(sessionKey: string): () => void {
  if (!sessionKey) return () => undefined;
  nextRequestId += 1;
  const requestId = nextRequestId;
  inFlight.set(requestId, sessionKey);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    inFlight.delete(requestId);
  };
}

/** 当前仍登记着在途回合的会话键（去重：同一会话多个在途请求只中断一次）。 */
export function inFlightChatSessions(): string[] {
  return [...new Set(inFlight.values())];
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
