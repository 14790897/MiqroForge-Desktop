/**
 * #1118：启动时从 `localStorage['miqi:lastSession']` 恢复「上次会话」的校验。
 *
 * 为什么需要校验：bridge 的 `sessions.get(key)` 对未知 key 走
 * `SessionManager.get_or_create`（miqi/runtime/session_handlers.py），**不报错、
 * 返回一个空会话**。所以「会话已被删除」和「会话存在但没有消息」在渲染层完全
 * 无法区分——App 会把一个不存在的 key 当成当前会话，界面照常渲染欢迎页，
 * 之后的新建/发送都落在那个幽灵 key 上，等于用被删会话的身份开新会话。
 *
 * 真实用户可达：删除当前会话的入口不止一处（SessionExplorer / 设置页的永久删除
 * 都不通知 App，见 #1118 第七轮复核），会话也可能在另一个实例或另一个工作区里
 * 被删掉；重启后 lastSession 就指向一个不存在的 key。
 *
 * 判定收拢成单点并导出，让回归测试直接锁定（同 ChatConsole 的
 * `shouldRenderReplyHeadThinking` 约定）。`sessions.list` 只列**已落盘**的会话
 * （空会话是临时的，不进列表），所以「上次会话是个从没落盘的空会话」也会判成
 * 幽灵——回退到默认态对用户无差别（两者渲染的都是欢迎页 + 首次发送即落盘）。
 */

/** 空态哨兵会话 key（与 App.tsx 初值 / ChatConsole 的 DEFAULT_SESSION 同字面量）。 */
export const DEFAULT_SESSION_KEY = 'desktop:default';

/**
 * 启动恢复的 key 是否应回退到默认态。
 *
 * @param restoredKey `localStorage['miqi:lastSession']` 读到的值（可能为 null）。
 * @param knownKeys   已知存在的会话 key：`sessions.list()` ∪ `sessions.listArchived()`
 *                    （归档会话仍然存在，不该被当成幽灵）。
 * @returns true 表示 restoredKey 查无此会话，应回退到默认态。
 *
 * 只对「明确查无此 key」的普通 key 返回 true：默认态哨兵本身不回退（它就是要
 * 回退到的目标），空值不动（读不到 localStorage 时初值已经是默认态）。
 */
export function shouldFallbackToDefaultSession(
  restoredKey: string | null | undefined,
  knownKeys: readonly string[],
  defaultKey: string = DEFAULT_SESSION_KEY
): boolean {
  if (!restoredKey) return false;
  if (restoredKey === defaultKey) return false;
  return !knownKeys.includes(restoredKey);
}
