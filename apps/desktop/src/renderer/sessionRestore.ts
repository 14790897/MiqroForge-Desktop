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
 *
 * #1118 第八轮：校验本身要**先于** ChatConsole 挂载（两阶段启动，见 App.tsx）。
 * 校验是异步的，而 ChatConsole 的加载 effect 会对 `sessionKey` 直接调
 * `sessions.get`（get-or-create）。第八轮实测确认裸 get 不会把幽灵落盘、也不会
 * 让它进 `sessions.list`（空会话 `exclude_empty=True` 被排除），所以第七轮的回退
 * 判定本身没被打穿；但顺序仍然是错的——`get(workspace=…)` 那种形状确实会落盘，
 * 让「先加载、后判定」依赖后端当前恰好是「空会话临时态」。两阶段把顺序钉死。
 *
 * #1118 第九轮（门兜底语义）：第八轮只钉住了「有结论才挂载」，没钉住**结论本身
 * 必须是有效的**。两条兜底路径（桥 10s 未就绪、`sessions.list` 抛错）当时都是
 * 「开闸但保留原 key」——门是开了，挂载的却是一个**从没验证过**的 key，等于把
 * 第八轮要掐掉的「get-or-create 先摸一遍幽灵」又放回来了。现在统一成：验证拿不到
 * 结论时**显式回退到默认哨兵**再放行（`verifyRestoredSession` → `unverified`，
 * 见 `resolveUnverifiedRestoreKey`）。用户的会话仍在侧边栏可选，代价只是停在
 * 欢迎页而不是幽灵会话里。
 *
 * #1118 第十轮（计时器起点）：第九轮把「没能验证」统一成回退默认，但如果这个
 * 结论是**计时器**在同意门还挡着的时候下的，回退的就不是「没能验证的 key」而是
 * 「还没开始验证的 key」——同意页上多读一会儿协议，上次的会话就没了。兜底计时器
 * 的武装条件因此收紧成「校验挂着 **且** 同意门已开」（`shouldArmRestoreTimeout`），
 * 预算从桥真正开始启动的那一刻起算。
 */

/** 空态哨兵会话 key（与 App.tsx 初值 / ChatConsole 的 DEFAULT_SESSION 同字面量）。 */
export const DEFAULT_SESSION_KEY = 'desktop:default';

/**
 * 启动恢复出来的 key 是否**需要先校验存在性**再交给 ChatConsole。
 *
 * 默认态哨兵不需要：它就是要回退到的目标，且全新 profile 下它本来也不在
 * `sessions.list` 里（校验只会平白多一次 IPC）。空值同理（读不到 localStorage
 * 时初值已经是哨兵）。其余 key 一律校验——存在性未知时不得让 ChatConsole
 * 用 get-or-create 的 `sessions.get` 先摸一遍。
 */
export function shouldVerifyRestoredSession(
  restoredKey: string | null | undefined,
  defaultKey: string = DEFAULT_SESSION_KEY
): boolean {
  if (!restoredKey) return false;
  return restoredKey !== defaultKey;
}

/**
 * 恢复门的**兜底计时器**该不该武装（#1118 第十轮 CR）。
 *
 * 计时器的语义是「等桥起来，等不到就回退默认」——它的预算必须从**桥开始启动**
 * 的那一刻起算。而桥的启动被隐私同意门挡在后面（App.tsx 的 consent-first：
 * `if (!consentOk) return;` 时不调 `runtime.start()`），所以同意前 `status.state`
 * 恒不为 `running`、存在性校验根本不跑；此时武装计时器，等于让用户在同意页上
 * 读协议的时间去消耗「等桥」的预算——停留超过 RESTORE_GATE_MAX_MS 就在校验
 * **开始之前**把恢复出来的 key 判负、降级成默认哨兵（`openGateUnverified` 还会
 * 顺手把 `restoredSessionCheckedRef` 置真，同意之后校验再也不会跑）。用户点了
 * 同意也回不到上次的会话。
 *
 * 因此：只有「恢复校验还挂着」**且**「同意门已开」两个条件同时成立才武装。
 * 同意门打开会让 effect 重跑（consentOk 在依赖里），届时预算从头算起。
 *
 * @param restorePending 恢复校验尚未出结论（默认哨兵/读不到 lastSession 时为 false）。
 * @param consentOk      隐私同意门已通过（或 E2E 绕过）。
 */
export function shouldArmRestoreTimeout(restorePending: boolean, consentOk: boolean): boolean {
  return restorePending && consentOk;
}

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

/**
 * 启动恢复校验的结论（三态，缺一不可）。
 *
 * - `keep`：验证成功——恢复的 key 确实存在，放行时就用它（默认哨兵/空值也走这里，
 *   它们本来就不需要验证）。
 * - `fallback`：**明确查无此 key**（幽灵）——回退默认哨兵。
 * - `unverified`：**没能拿到结论**（list/listArchived 执行失败、重试耗尽）。
 *   与 `fallback` 区别在于原因不同、日志不同；处置相同——**一律回退默认哨兵**。
 *   第九轮的缺陷就是把它当成「按 keep 处理」。
 */
export type RestoreVerdict = 'keep' | 'fallback' | 'unverified';

/** 校验执行失败时的重试次数（含首次）。桥刚起时 list 可能瞬时失败，重试能救回来。 */
export const RESTORE_VERIFY_ATTEMPTS = 3;
/** 重试退避基数：第 n 次重试前等 `n × 该值`（250ms / 500ms）。 */
export const RESTORE_VERIFY_BACKOFF_MS = 250;

/**
 * 校验启动恢复出来的 key 是否还在，带**有界重试**。
 *
 * `loadKnownKeys` 抛错（bridge 还没起好、IPC 通道瞬时失败、返回体形状不对）时按
 * 退避重试，重试耗尽返回 `unverified`——调用方据此显式回退默认哨兵，**不得**
 * 带着这个没验证过的 key 放行。
 *
 * 不变量：本函数**不抛**（错误在内部收敛成 `unverified`），且对默认哨兵/空值
 * 直接返回 `keep` 而**不调用** `loadKnownKeys`（省一次 IPC，与
 * `shouldVerifyRestoredSession` 同一口径）。
 */
export async function verifyRestoredSession(
  restoredKey: string | null | undefined,
  loadKnownKeys: () => Promise<readonly string[]>,
  opts: {
    attempts?: number;
    backoffMs?: number;
    sleep?: (ms: number) => Promise<void>;
  } = {}
): Promise<RestoreVerdict> {
  if (!shouldVerifyRestoredSession(restoredKey)) return 'keep';
  const key = restoredKey as string;
  const attempts = Math.max(1, opts.attempts ?? RESTORE_VERIFY_ATTEMPTS);
  const backoffMs = Math.max(0, opts.backoffMs ?? RESTORE_VERIFY_BACKOFF_MS);
  const sleep =
    opts.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const knownKeys = await loadKnownKeys();
      return shouldFallbackToDefaultSession(key, knownKeys) ? 'fallback' : 'keep';
    } catch {
      // 失败=没有结论，不是「不存在」：重试到上限再交回调用方处置。
      if (attempt === attempts) return 'unverified';
      await sleep(backoffMs * attempt);
    }
  }
  return 'unverified'; // 循环必在内部 return，这里只为类型收敛
}

/**
 * 「没能验证」时该挂载哪个会话 key（门兜底语义的唯一出口）。
 *
 * 返回 `currentKey` 只有一种情况：**用户已经切走**（当前 key 不是启动时恢复的那
 * 一个）——那是用户自己的选择，交棒，不动。
 *
 * 否则一律返回 `defaultKey`：**绝不返回那个没验证过的 key**。这是第九轮 CR 要求
 * 的核心不变量——放行一个未验证的非默认 key，等于让 ChatConsole 的
 * get-or-create `sessions.get` 先把它当正常会话摸一遍（切走时还会被空会话 GC
 * 当成「上一个会话」删掉），正是第八轮两阶段启动要掐掉的形状。
 */
export function resolveUnverifiedRestoreKey(
  restoredKey: string | null | undefined,
  currentKey: string,
  defaultKey: string = DEFAULT_SESSION_KEY
): string {
  if (currentKey !== restoredKey) return currentKey; // 用户已切走
  return defaultKey;
}
