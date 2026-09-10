/**
 * 资产面板拖宽「窗口跟随」队列的竞态回归（#989）。
 *
 * 重点锁三件曾经出过问题的事：
 * 1. 松手时还有请求在途 → 必须等队列静默、按**最终实际**应用到的宽度收尾
 *    （CodeRabbit 复查指出：旧实现松手即撤锚点，在途响应回来时锚点已没了，
 *    窗口扩了而面板和 panelWidth 都没跟上）。
 * 2. 窗口拒绝跟随（最大化/满屏，主进程返回 skipped）→ 面板退回「自己变宽、
 *    聊天列让位」的老行为，而不是原地不动。
 * 3. 窗口只应用了一部分（触到屏幕边界）→ 收尾用实际值，不是用户拖到的目标值。
 */
import { describe, expect, it } from 'vitest';
import {
  clampPanelWidth,
  createPanelWindowSync,
  panelWidthForApplied,
  PANEL_MAX_WIDTH,
  PANEL_MIN_WIDTH,
  type PanelDragAnchor,
  type PanelWindowExtraResult,
} from './panelWindowSync';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function makeHarness() {
  const sent: number[] = [];
  const inFlight: Array<{
    extra: number;
    settle: (result: PanelWindowExtraResult) => void;
  }> = [];
  const widths: number[] = [];
  const committed: number[] = [];
  const scheduled: Array<() => void> = [];
  const sync = createPanelWindowSync({
    send: (extra) => {
      sent.push(extra);
      const d = deferred<PanelWindowExtraResult>();
      inFlight.push({ extra, settle: d.resolve });
      return d.promise;
    },
    applyWidth: (width) => widths.push(width),
    commitWidth: (width) => committed.push(width),
    schedule: (cb) => {
      scheduled.push(cb);
      return scheduled.length; // 句柄 = 下标 + 1
    },
    cancel: (handle) => {
      if (handle > 0) scheduled[handle - 1] = () => {};
    },
  });
  /** 跑掉排队的 rAF 回调。 */
  const flush = () => scheduled.splice(0).forEach((cb) => cb());
  /** 让第 index 次请求返回结果，并把微任务跑完。 */
  const respond = async (index: number, result: PanelWindowExtraResult) => {
    inFlight[index].settle(result);
    await tick();
  };
  return { sync, sent, inFlight, widths, committed, flush, respond };
}

describe('panelWindowSync 宽度换算', () => {
  it('面板宽度钳制在 [200, 500]', () => {
    expect(clampPanelWidth(120)).toBe(PANEL_MIN_WIDTH);
    expect(clampPanelWidth(900)).toBe(PANEL_MAX_WIDTH);
    expect(clampPanelWidth(327.6)).toBe(328);
  });
  it('按窗口实际加宽量反推面板宽（相对锚点，不用绝对宽）', () => {
    const anchor: PanelDragAnchor = {
      clientX: 500,
      width: 360,
      applied: 80,
      released: false,
      targetWidth: 360,
      windowFollowed: true,
    };
    // 窗口再多扩 40 → 面板 400（不是 80+40）
    expect(panelWidthForApplied(anchor, 120)).toBe(400);
    // 窗口没动 → 面板回到锚点宽
    expect(panelWidthForApplied(anchor, 80)).toBe(360);
  });
});

describe('panelWindowSync 拖拽队列', () => {
  it('松手时请求仍在途：等队列静默后按最终 applied 收尾（不错位）', async () => {
    const h = makeHarness();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(360);
    h.flush();
    expect(h.sent).toEqual([80]); // 窗口加宽目标 = 锚点 applied(0) + 面板增量(80)

    // 松手：此刻 80 那次请求还没回来，applied 仍是旧值 0
    h.sync.endDrag();
    expect(h.committed).toEqual([]); // 队列未静默 → 不能收尾
    expect(h.widths).toEqual([]); // 更不能按旧 applied 定格面板

    await h.respond(0, { applied: 80 });
    h.flush();
    await tick();

    // 面板与提交宽度都落到最终实际值，锚点撤掉
    expect(h.widths).toEqual([360]);
    expect(h.committed).toEqual([360]);
    expect(h.sync.anchor).toBeNull();
    expect(h.sent).toEqual([80]); // 收尾目标与在途目标相同 → 不重发
  });

  it('窗口拒绝跟随（最大化 skipped）：面板按用户拖到的宽度定格', async () => {
    const h = makeHarness();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(360);
    h.flush();
    await h.respond(0, { applied: 0, skipped: true });
    // 窗口没动，但面板跟手——否则最大化下拖分隔条完全不动
    expect(h.widths).toEqual([360]);
    expect(h.sync.applied).toBe(0); // skipped 的 applied=0 不能当成「应用到了 0」

    h.sync.endDrag();
    h.flush();
    await tick();
    expect(h.committed).toEqual([360]);
    expect(h.sync.anchor).toBeNull();
  });

  it('窗口只应用一部分（触到屏幕边界）：收尾用实际值而非目标值', async () => {
    const h = makeHarness();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(400);
    h.flush();
    await h.respond(0, { applied: 60 }); // 目标 120，屏幕只让扩 60
    expect(h.widths).toEqual([340]);

    h.sync.endDrag();
    h.flush();
    await tick();
    expect(h.committed).toEqual([340]); // 不是 400
    expect(h.sync.anchor).toBeNull();
  });

  it('latest-wins：在途期间连续拖动只保留最新目标，同一时刻至多一个在途', async () => {
    const h = makeHarness();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(320);
    h.flush();
    expect(h.sent).toEqual([40]);

    h.sync.dragTo(340);
    h.sync.dragTo(360);
    h.flush();
    expect(h.sent).toEqual([40]); // 在途 → 不重发，只记 pending

    await h.respond(0, { applied: 40 });
    h.flush();
    expect(h.sent).toEqual([40, 80]); // 补发到最新
    await h.respond(1, { applied: 80 });
    expect(h.widths).toEqual([320, 360]);
  });

  it('只点一下分隔条不拖动：窗口请求与当前一致，面板不跳变', async () => {
    const h = makeHarness();
    h.sync.request(280);
    h.flush();
    await h.respond(0, { applied: 280 }); // 面板打开时窗口已扩到 280

    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.endDrag();
    h.flush();
    await tick();
    expect(h.sent).toEqual([280]); // 目标没变 → 不重发
    expect(h.widths).toEqual([]); // 未拖动 → 不写面板宽度
    expect(h.committed).toEqual([280]); // 收尾仍提交当前宽，供开关面板复用
  });

  it('开关面板的 request 不触碰面板宽度（没有拖拽锚点）', async () => {
    const h = makeHarness();
    h.sync.request(280);
    h.flush();
    await h.respond(0, { applied: 280 });
    h.sync.request(0);
    h.flush();
    await h.respond(1, { applied: 0 });
    expect(h.sent).toEqual([280, 0]);
    expect(h.widths).toEqual([]);
    expect(h.committed).toEqual([]);
    expect(h.sync.applied).toBe(0);
  });

  it('dispose 取消已排队的请求', () => {
    const h = makeHarness();
    h.sync.request(300);
    h.sync.dispose(); // 卸载时撤销排队中的 rAF
    h.flush();
    expect(h.sent).toEqual([]);
  });

  it('dispose 之后实例仍可复用（React StrictMode 的 mount → 卸载 → 再 mount）', async () => {
    // dev 下 main.tsx 常开 StrictMode，effect 会被跑成 mount → cleanup → mount。
    // dispose 若置永久停用标志，第二次挂载后面板就再也不跟随窗口了。
    const h = makeHarness();
    h.sync.dispose();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(360);
    h.flush();
    expect(h.sent).toEqual([80]);
    await h.respond(0, { applied: 80 });
    expect(h.widths).toEqual([360]);
  });
});
