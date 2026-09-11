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
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function makeHarness() {
  const sent: number[] = [];
  const inFlight: Array<{
    extra: number;
    settle: (result: PanelWindowExtraResult) => void;
    fail: (reason?: unknown) => void;
  }> = [];
  const widths: number[] = [];
  const committed: number[] = [];
  const scheduled: Array<() => void> = [];
  let settledCount = 0;
  const sync = createPanelWindowSync({
    send: (extra) => {
      sent.push(extra);
      const d = deferred<PanelWindowExtraResult>();
      inFlight.push({ extra, settle: d.resolve, fail: d.reject });
      return d.promise;
    },
    applyWidth: (width) => widths.push(width),
    commitWidth: (width) => committed.push(width),
    onRequestSettled: () => {
      settledCount += 1;
    },
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
  return {
    sync,
    sent,
    inFlight,
    widths,
    committed,
    flush,
    respond,
    settled: () => settledCount,
  };
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

  it('旧生命周期的在途响应不会污染新操作（dispose → 新拖拽 → 旧响应才回来）', async () => {
    // 这条正是「dispose 可复用」打开的窗口：StrictMode 复用同一实例，
    // 旧生命周期发出去的 IPC 之后才 resolve，若不按代次作废，它会：
    //   ① 把旧的 applied 写进新状态；② 命中 anchor 后按旧值改面板宽度。
    const h = makeHarness();
    h.sync.request(100);
    h.flush();
    expect(h.sent).toEqual([100]); // 旧生命周期的请求还在飞

    h.sync.dispose(); // 模拟 StrictMode 卸载
    h.sync.beginDrag({ clientX: 500, width: 280 }); // 重挂载后用户开始拖拽
    h.sync.dragTo(360);
    h.flush();
    expect(h.sent).toEqual([100, 80]); // 新请求已发出

    await h.respond(0, { applied: 999 }); // 旧响应现在才回来，带着一个离谱的 applied
    expect(h.sync.applied).toBe(0); // 未被写成 999
    expect(h.widths).toEqual([]); // 也没按它改面板宽度

    await h.respond(1, { applied: 80 }); // 新响应才生效
    expect(h.widths).toEqual([360]);
    expect(h.sync.applied).toBe(80);
  });

  it('dispose 清掉去重位：新生命周期里同一个目标必须重新发', async () => {
    // 卸载时 ChatConsole 会自行 setPanelWindowExtra(0) 把主进程 extra 归零，
    // 模块里的 requested 若跨 dispose 残留，新生命周期里同一个目标会被
    // 「已请求过」直接吞掉 —— React 以为窗口还有 extra，主进程其实已经归零。
    const h = makeHarness();
    h.sync.request(280);
    h.flush();
    await h.respond(0, { applied: 280 });
    expect(h.sync.applied).toBe(280);
    expect(h.sent).toEqual([280]);

    h.sync.dispose();
    expect(h.sync.applied).toBe(0); // 基线跟着回到新生命周期

    h.sync.request(280); // 同一目标
    h.flush();
    expect(h.sent).toEqual([280, 280]); // 必须重发
    await h.respond(1, { applied: 280 });
    expect(h.sync.applied).toBe(280);
  });

  it('dispose 清掉 applied：新生命周期的拖拽按正确基线算窗口增量', async () => {
    // 这是 applied 残留真正的杀伤面：拖拽的窗口加宽量是「锚点 applied + 面板增量」
    // 的相对量，锚点取到脏的 280 会让主进程多扩整整一个面板宽。
    const h = makeHarness();
    h.sync.request(280);
    h.flush();
    await h.respond(0, { applied: 280 });

    h.sync.dispose();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(360); // 面板只加了 80
    h.flush();

    expect(h.sent).toEqual([280, 80]); // 不是 280 + 80 = 360
  });

  it('request 落地后回调 onRequestSettled（应用 / 跳过 / 去重命中都算）', async () => {
    const h = makeHarness();
    // 正常应用
    h.sync.request(280);
    h.flush();
    expect(h.settled()).toBe(0); // 未落地前不回调
    await h.respond(0, { applied: 280 });
    expect(h.settled()).toBe(1);

    // 被主进程跳过（最大化）—— 也必须回调，否则面板永远不显示
    h.sync.request(0);
    h.flush();
    await h.respond(1, { applied: 0, skipped: true });
    expect(h.settled()).toBe(2);

    // 目标与上次相同 → 走去重分支，同样要回调
    h.sync.request(0);
    h.flush();
    expect(h.settled()).toBe(3);

    // 请求失败（IPC 抛错）也不能把面板卡住不显示
    h.sync.request(300);
    h.flush();
    h.inFlight[2].fail(new Error('ipc down'));
    await tick();
    expect(h.settled()).toBe(4);
  });

  it('拖拽在途时排队的 request：必须等它真的发出去才通知（否则面板又先出现）', async () => {
    const h = makeHarness();
    h.sync.beginDrag({ clientX: 500, width: 280 });
    h.sync.dragTo(360);
    h.flush();
    expect(h.sent).toEqual([80]); // 拖拽的请求在途

    h.sync.request(280); // 在途期间排进一个独立目标
    expect(h.settled()).toBe(0);

    await h.respond(0, { applied: 80 }); // 在途请求回来；finally 里只是排了 rAF
    expect(h.sent).toEqual([80]); // 排队的目标还没发出去
    expect(h.settled()).toBe(0); // 所以此刻不能通知 —— 否则窗口还没扩面板就显示了

    h.flush(); // 现在才真的发出
    expect(h.sent).toEqual([80, 280]);
    await h.respond(1, { applied: 280 });
    expect(h.settled()).toBe(1); // 落地后才通知
  });

  it('请求失败不占去重位：同一目标之后还能重发', async () => {
    const h = makeHarness();
    h.sync.request(280);
    h.flush();
    expect(h.sent).toEqual([280]);
    h.inFlight[0].fail(new Error('ipc down'));
    await tick();

    h.sync.request(280); // 同一目标重试
    h.flush();
    expect(h.sent).toEqual([280, 280]); // 不能被去重吞掉，否则窗口永远补不回来
    await h.respond(1, { applied: 280 });
    expect(h.sync.applied).toBe(280);
  });
});
