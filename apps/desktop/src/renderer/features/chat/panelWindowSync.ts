/**
 * 资产面板拖宽的「窗口跟随」串行队列（#989）。
 *
 * 资产面板变宽时会请求主进程把原生窗口同量加宽，聊天列（flex-1）因此分到新增
 * 宽度而保持原宽、内容不重排。窗口加宽是异步的（IPC + 原生 setBounds），所以
 * 这个队列要同时兜住四件事：
 *
 * - **latest-wins**：每帧至多发一次请求，且同一时刻至多一个在途。拖拽中快速来回
 *   不会把几十个窗口 resize 塞进主进程排队。
 * - **面板跟随 applied**：面板宽度按主进程**实际应用到的**增量走，不超前于窗口
 *   扩出。面板是消息树兄弟节点，DOM 先宽、窗口后宽会把聊天列瞬时压扁。
 * - **松手后按最终实际值收尾**：松手那一刻可能还有一次请求在途，此时 `applied`
 *   还是旧值。按旧值把面板钉住，等窗口真的动完就错位（窗口宽、面板窄或反之）。
 *   所以松手只置 `released`，等队列静默后再由 settle 用最终 `applied` 提交宽度。
 * - **窗口拒绝跟随时退回老行为**：最大化/满屏/不可缩放时主进程返回 `skipped`，
 *   面板仍按用户拖到的宽度走（聊天列让位），而不是原地不动。
 *
 * 纯状态机 + 注入的 send/调度器，便于在 node 环境下直接测竞态。
 */

/** 资产面板宽度边界：拖拽与收尾共用同一组钳制，避免两处用了不同上下限、
 *  松手瞬间面板跳一下。 */
export const PANEL_MIN_WIDTH = 200;
export const PANEL_MAX_WIDTH = 500;

export function clampPanelWidth(width: number): number {
  return Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, Math.round(width)));
}

/** 分隔条的一次拖拽。窗口跟随加宽按**相对量**换算：面板宽 = 锚点宽 +
 *  (窗口实际加宽 − 锚点时的加宽)。用绝对宽会在冷启动默认面板已占空间时让
 *  窗口多扩一整块。 */
export interface PanelDragAnchor {
  /** 按下时的鼠标 x */
  clientX: number;
  /** 按下时的面板宽 */
  width: number;
  /** 按下时主进程已应用到的窗口加宽 */
  applied: number;
  /** 松手后置位：队列停稳前锚点不撤，否则在途响应回来时锚点已没了，
   *  最终宽度既到不了面板 DOM 也回不到 panelWidth。 */
  released: boolean;
  /** 用户最后拖到的宽度（窗口拒绝跟随时面板按它定格）。 */
  targetWidth: number;
  /** 本次拖拽中窗口是否真的跟随加宽过。 */
  windowFollowed: boolean;
}

/** 主进程实际应用到的窗口加宽 → 面板应有的宽度。 */
export function panelWidthForApplied(anchor: PanelDragAnchor, applied: number): number {
  return clampPanelWidth(anchor.width + (applied - anchor.applied));
}

export interface PanelWindowExtraResult {
  applied: number;
  skipped?: boolean;
}

export interface PanelWindowSyncOptions {
  /** 请求主进程把窗口加宽到 extra 像素。 */
  send: (extra: number) => Promise<PanelWindowExtraResult>;
  /** 把面板改到该宽度。拖拽中直接改 DOM，避免每帧重渲染整棵消息树。 */
  applyWidth: (width: number) => void;
  /** 收尾时提交宽度（setState），供开关面板等复用。 */
  commitWidth: (width: number) => void;
  /** `request()` 的目标落地后回调（已应用 / 被跳过 / 去重命中都算）。
   *  开面板用它把「窗口先让出宽度」和「面板再出现」串成一个过渡，
   *  避免面板先渲染、聊天列被压窄一瞬再弹回。拖拽路径不触发。 */
  onRequestSettled?: () => void;
  /** 调度下一批（默认 requestAnimationFrame）。 */
  schedule?: (cb: () => void) => number;
  cancel?: (handle: number) => void;
}

export interface PanelWindowSync {
  /** 当前拖拽锚点；未处于拖拽中时为 null。 */
  readonly anchor: Readonly<PanelDragAnchor> | null;
  /** 主进程当前已应用到的窗口加宽量。 */
  readonly applied: number;
  /** 开始一次拖拽。 */
  beginDrag(input: { clientX: number; width: number }): void;
  /** 拖拽中把面板拖到 width，并请求窗口同量跟随。 */
  dragTo(width: number): void;
  /** 松手：等队列静默后按窗口最终实际应用到的宽度收尾。 */
  endDrag(): void;
  /** 与拖拽无关的窗口加宽请求（开/关面板）。 */
  request(extra: number): void;
  /** 停掉排队的请求、作废在途响应的写回权，并清掉拖拽锚点（组件卸载）。
   *  实例之后仍可继续使用——见 dispose 实现里的 StrictMode 说明。 */
  dispose(): void;
}

export function createPanelWindowSync(options: PanelWindowSyncOptions): PanelWindowSync {
  const {
    send,
    applyWidth,
    commitWidth,
    onRequestSettled,
    schedule = (cb) => requestAnimationFrame(cb),
    cancel = (handle) => cancelAnimationFrame(handle),
  } = options;

  let raf = 0;
  /** 待发的窗口加宽目标（NaN = 无）。 */
  let pending = NaN as number;
  /** 与 pending 同批的「用户想拖到的面板宽」；窗口拒绝跟随时按它定格面板。 */
  let pendingWidth = NaN as number;
  let inFlight = false;
  let applied = 0;
  /** 生命周期代次：dispose 时 +1。在途 IPC 发出时记下当时的代次，回来时代次
   *  对不上就整条丢弃——否则「旧实例的请求 → dispose → 新操作 → 旧响应才回来」
   *  会把上一个生命周期的 applied 写进当前状态，污染新拖拽。（StrictMode 的
   *  模拟卸载/重挂载会复用同一个实例，这条路径是真会走到的。） */
  let generation = 0;
  /** 本次 request() 是否还需要回调 onRequestSettled。 */
  let notifyOnSettle = false;
  /** 上一次发出的目标（含被主进程跳过的），仅用于去重。不能拿 applied 去重，
   *  否则被跳过的目标会每帧重发。 */
  let requested = NaN as number;
  let anchor: PanelDragAnchor | null = null;
  /** 上一次写进 DOM 的宽度，重复值不再写（点击分隔条不拖动、收尾回落到同一
   *  宽度时都会走到这里，没必要再触发一次样式写入）。 */
  let lastAppliedWidth = NaN as number;

  const apply = (width: number) => {
    if (width === lastAppliedWidth) return;
    lastAppliedWidth = width;
    applyWidth(width);
  };

  /** request() 的目标已落地 → 通知一次（幂等，避免重复触发面板显示）。
   *  必须等队列真的静默才回调：`request()` 可能是排在一次在途请求后面进来的，
   *  此时目标还没发出去，提前回调会让面板先出现、窗口后扩——正是要消掉的那个
   *  挤压。判据与 settle() 的静默判据一致。 */
  const notifyRequestSettled = () => {
    if (!notifyOnSettle) return;
    if (inFlight || raf || Number.isFinite(pending)) return; // 目标还没发出去
    notifyOnSettle = false;
    onRequestSettled?.();
  };

  /** 队列静默且已松手 → 按主进程最终实际应用到的宽度收尾。 */
  const settle = () => {
    if (!anchor || !anchor.released) return;
    if (inFlight || raf || Number.isFinite(pending)) return; // 队列未静默
    // 窗口没跟随（最大化/满屏被跳过）→ 退回「面板自己变宽、聊天列让位」，
    // 拖到哪就是哪；跟随时用实际加宽量反推，面板宽度与窗口增量恒等。
    const settled = anchor.windowFollowed
      ? panelWidthForApplied(anchor, applied)
      : clampPanelWidth(anchor.targetWidth);
    apply(settled);
    commitWidth(settled);
    anchor = null;
  };

  const maybeQueue = () => {
    // 没活干就别排 rAF：排了会让「队列静默」的判据（raf 非零）永远不成立，
    // settle() / notifyRequestSettled() 就再也不会被触发。
    if (raf || inFlight || !Number.isFinite(pending)) return;
    raf = schedule(() => {
      raf = 0;
      const target = pending;
      const desired = pendingWidth;
      if (!Number.isFinite(target)) return;
      pending = NaN;
      pendingWidth = NaN;
      if (target === requested) {
        settle(); // 目标没变：窗口已停在那里，直接收尾
        notifyRequestSettled();
        return;
      }
      inFlight = true;
      const gen = generation;
      send(target)
        .then((r) => {
          // 上一个生命周期的响应：整条丢弃，不写 applied、不碰面板宽度。
          if (gen !== generation) return;
          // 去重位只在**请求真的落地后**才占：若写在 send 之前，一次失败的请求
          // 也会把目标记成「已请求过」，之后同一个目标会被去重直接吞掉、永远补不
          // 回来，面板与窗口的宽度就此错开。失败走 catch，requested 保持原值，
          // 于是同一目标下次还能重发。
          requested = target;
          // skipped：最大化/满屏/不可缩放/屏幕已无空间，窗口根本没动。此时
          // r.applied 是 0（不是「应用到了 0」），回写它会让拖拽中的面板按 0
          // 反推宽度而跳变。面板本身仍要跟手——按用户拖到的宽度走。
          if (r.skipped) {
            if (anchor && Number.isFinite(desired)) apply(clampPanelWidth(desired));
            return;
          }
          applied = r.applied;
          if (anchor) {
            anchor.windowFollowed = true;
            // 拖拽中面板只按实际增量走，不超前于窗口扩出。
            apply(panelWidthForApplied(anchor, r.applied));
          }
        })
        .catch(() => {
          /* 请求失败也让队列继续（finally 里补发 / 收尾） */
        })
        .finally(() => {
          if (gen !== generation) return; // 已 dispose：既别补发也别收尾
          inFlight = false;
          maybeQueue(); // 在途期间又收到更新宽度 → 补发到最新
          settle(); // 在途期间松了手 → 现在才轮到收尾
          notifyRequestSettled();
        });
    });
  };

  return {
    get anchor() {
      return anchor;
    },
    get applied() {
      return applied;
    },
    beginDrag({ clientX, width }) {
      anchor = {
        clientX,
        width,
        applied,
        released: false,
        targetWidth: width,
        windowFollowed: false,
      };
      // 面板此刻就在这个宽度上（按下点即分隔条），不必再写一次。
      lastAppliedWidth = clampPanelWidth(width);
    },
    dragTo(width) {
      if (!anchor) return;
      anchor.targetWidth = width;
      pending = Math.round(anchor.applied + (width - anchor.width));
      pendingWidth = width;
      maybeQueue();
    },
    endDrag() {
      if (!anchor) return;
      anchor.released = true;
      pending = Math.round(anchor.applied + (anchor.targetWidth - anchor.width));
      pendingWidth = anchor.targetWidth;
      maybeQueue();
      settle();
    },
    request(extra) {
      pending = Math.round(extra);
      pendingWidth = NaN;
      notifyOnSettle = true;
      maybeQueue();
    },
    dispose() {
      // 不置永久停用标志：React StrictMode（dev 下 main.tsx 常开）会把 effect 跑成
      // mount → 卸载 → 再 mount，永久停用会让第二次挂载之后面板再也不跟随窗口。
      // 所以这里只做两件事：停掉排队中的工作，以及 **作废在途响应的写回权**
      // （generation +1）——同一实例被 StrictMode 复用后，旧生命周期那个还在飞的
      // 响应回来时会命中代次检查被整条丢弃，不会把旧 applied 写进新拖拽。
      generation += 1;
      if (raf) cancel(raf);
      raf = 0;
      pending = NaN;
      pendingWidth = NaN;
      notifyOnSettle = false;
      // 旧请求的 finally 已被代次挡掉，不会再来清 inFlight；这里主动放开，
      // 否则新生命周期第一次请求会被「有在途」卡住。
      inFlight = false;
      anchor = null;
      // 去重位与实际宽度也必须回到新生命周期的基线，不能跨 dispose 残留：
      //   · requested 残留 → 新生命周期里同一个目标会被当成「已请求过」直接吞掉；
      //   · applied 残留 → 新拖拽的 anchor.applied 取到脏值，窗口加宽量按错的
      //     基线算（实测会多扩整整一个面板宽）。
      // 卸载时 ChatConsole 会自行把主进程 extra 归零，模块这边必须同步归零。
      requested = NaN;
      applied = 0;
      lastAppliedWidth = NaN;
    },
  };
}
