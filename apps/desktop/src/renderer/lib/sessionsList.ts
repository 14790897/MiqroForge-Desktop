import type { SessionInfo } from '../../shared/ipc';

/** `sessions.list` 的返回。失败/超时时主进程原样返回 `null`（见 #1191）。 */
export type SessionsListResponse = { sessions: SessionInfo[] } | null | undefined;

/**
 * 拿到 `sessions.list` 的返回后，该用它覆盖现有列表，还是保留原样？
 *
 * 返回 `null` 表示「保留现有列表」。
 *
 * 失败（超时 / 桥不可用）时主进程返回 `null`；#1191 之前这个 `null` 被折叠成
 * `{sessions: []}`，渲染层据此清空列表，把用户已有的会话从眼前抹掉。后端
 * **确实**没有会话时给的是 `{sessions: []}`——空数组与失败必须分开对待，
 * 所以这里只挡 `null`，不挡空数组。
 *
 * 抽成纯函数是为了可测：组件整体渲染依赖大量 `window.miqi` mock，
 * 而这条判断正是回归点（#1191 的 Sidebar / #1202 的 SessionExplorer）。
 */
export function resolveSessionsList(r: SessionsListResponse): SessionInfo[] | null {
  return r && Array.isArray(r.sessions) ? r.sessions : null;
}
