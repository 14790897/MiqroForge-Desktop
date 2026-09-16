import { describe, it, expect } from 'vitest';
import {
  CrashRecoveryTracker,
  MAX_RELOADS_PER_WINDOW,
  NOTICE_TTL_MS,
  RELOAD_WINDOW_MS,
  evaluateReloadBudget,
  reloadLogLine,
  reloadSkippedLogLine,
} from '../src/main/crashRecovery';

/**
 * #1035 渲染进程崩溃恢复：预算纯函数、日志行格式、主进程侧状态表。
 *
 * 本文件只碰纯逻辑——`handleRendererCrash` 在调用点才动态 import Electron
 * （见 crashRecovery.ts 顶部注释），所以这里 import 本模块不会触发
 * `src/shared/electron.ts` 的 trampoline 校验。
 */

const T0 = 1_700_000_000_000; // 固定时间基准，避免依赖真实时钟

describe('evaluateReloadBudget — 10 分钟窗口 / 最多 3 次', () => {
  it('空历史：允许重载，序号从 1 起', () => {
    const budget = evaluateReloadBudget([], T0);
    expect(budget).toEqual({ allowed: true, attempt: 1, recent: [] });
  });

  it('0~3 次历史：预算内，attempt = 已有次数 + 1', () => {
    for (let n = 0; n < MAX_RELOADS_PER_WINDOW; n += 1) {
      const history = Array.from({ length: n }, (_, i) => T0 - (n - i) * 1000);
      const budget = evaluateReloadBudget(history, T0);
      expect(budget.allowed).toBe(true);
      expect(budget.attempt).toBe(n + 1);
      expect(budget.recent).toHaveLength(n);
    }
  });

  it('已达上限（3 次）：超预算，停止自动重载', () => {
    const history = [T0 - 3000, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.allowed).toBe(false);
    expect(budget.attempt).toBe(4); // 序号仍递增，便于日志排查
    expect(budget.recent).toHaveLength(MAX_RELOADS_PER_WINDOW);
  });

  it('超过上限（4 次）：仍然超预算', () => {
    const history = [T0 - 4000, T0 - 3000, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.allowed).toBe(false);
    expect(budget.recent).toHaveLength(4);
  });

  it('过期边界：年龄恰好等于窗口宽度的记录已过期（< 而非 <=）', () => {
    const exactlyAtEdge = T0 - RELOAD_WINDOW_MS;
    const budget = evaluateReloadBudget([exactlyAtEdge], T0);
    expect(budget.recent).toEqual([]);
    expect(budget.allowed).toBe(true);
    expect(budget.attempt).toBe(1);
  });

  it('过期边界内 1ms：仍算在窗口内', () => {
    const justInside = T0 - RELOAD_WINDOW_MS + 1;
    const budget = evaluateReloadBudget([justInside], T0);
    expect(budget.recent).toEqual([justInside]);
    expect(budget.attempt).toBe(2);
  });

  it('混合：只统计窗口内的记录，过期项被丢弃后预算自动恢复', () => {
    const history = [T0 - RELOAD_WINDOW_MS - 1, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.recent).toHaveLength(2);
    expect(budget.allowed).toBe(true);
    expect(budget.attempt).toBe(3);
  });
});

describe('日志行格式（期望行为 2：可检索）', () => {
  it('重载日志行含 renderer-reloaded / attempt / reason', () => {
    expect(reloadLogLine('oom', 1)).toBe('[main] renderer-reloaded: attempt=1 reason=oom');
    expect(reloadLogLine('crashed', 3)).toBe('[main] renderer-reloaded: attempt=3 reason=crashed');
  });

  it('超预算时没有重载，但同样留下可检索的痕迹', () => {
    const line = reloadSkippedLogLine('oom', MAX_RELOADS_PER_WINDOW);
    expect(line).toContain('[main] renderer-reload-skipped:');
    expect(line).toContain('reason=oom');
    expect(line).toContain(`reloadsInWindow=${MAX_RELOADS_PER_WINDOW}`);
    expect(line).toContain(`max=${MAX_RELOADS_PER_WINDOW}`);
  });
});

describe('CrashRecoveryTracker — 在飞 turn 登记表', () => {
  it('mark / settle：登记后可见，settle 后摘除', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.markTurnStarted('desktop:default', T0);
    tracker.markTurnStarted('folder:abc', T0);
    expect(tracker.getInFlightSessionKeys().sort()).toEqual(['desktop:default', 'folder:abc']);

    tracker.markTurnSettled('desktop:default');
    expect(tracker.getInFlightSessionKeys()).toEqual(['folder:abc']);
  });

  it('settle 未登记的会话是幂等的 no-op', () => {
    const tracker = new CrashRecoveryTracker();
    expect(() => tracker.markTurnSettled('never-started')).not.toThrow();
    expect(tracker.getInFlightSessionKeys()).toEqual([]);
  });

  it('空 sessionKey 不登记（避免脏 key 混进恢复提示）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.markTurnStarted('');
    expect(tracker.getInFlightSessionKeys()).toEqual([]);
  });

  it('turn 结束后重建再崩溃：提示里不再含已结束的会话', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.markTurnStarted('folder:a', T0);
    tracker.markTurnSettled('folder:a');
    tracker.markTurnStarted('folder:b', T0 + 1000);

    const { notice } = tracker.onRendererCrash('oom', -536870904, T0 + 2000);
    expect(notice.inFlightSessionKeys).toEqual(['folder:b']);
  });
});

describe('CrashRecoveryTracker — 崩溃记账与预算', () => {
  it('预算内连续崩溃：attempt 递增', () => {
    const tracker = new CrashRecoveryTracker();
    expect(tracker.onRendererCrash('oom', 1, T0).notice.attempt).toBe(1);
    expect(tracker.onRendererCrash('oom', 1, T0 + 1000).notice.attempt).toBe(2);
    expect(tracker.onRendererCrash('oom', 1, T0 + 2000).notice.attempt).toBe(3);
  });

  it('第 4 次崩溃超预算：不记账、attempt=0（渲染层与日志都能看出没自动重载）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    tracker.onRendererCrash('oom', 1, T0 + 1000);
    tracker.onRendererCrash('oom', 1, T0 + 2000);

    const fourth = tracker.onRendererCrash('oom', 1, T0 + 3000);
    expect(fourth.budget.allowed).toBe(false);
    expect(fourth.notice.attempt).toBe(0);
    // 未记账 ⇒ 窗口内的记录仍是 3 条，超预算对话框据此报数
    expect(fourth.budget.recent).toHaveLength(MAX_RELOADS_PER_WINDOW);

    // 下一次仍超预算（没有因为 attempt=0 而把预算"用掉"或重置）
    const fifth = tracker.onRendererCrash('oom', 1, T0 + 4000);
    expect(fifth.budget.allowed).toBe(false);
  });

  it('窗口是滑动的：只滑出一部分时预算已恢复，但 attempt 接着窗口内剩余次数算', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    tracker.onRendererCrash('oom', 1, T0 + 1000);
    tracker.onRendererCrash('oom', 1, T0 + 2000);
    expect(tracker.onRendererCrash('oom', 1, T0 + 3000).budget.allowed).toBe(false);

    // 只够让 T0 那条过期，T0+1000 / T0+2000 仍在窗口内
    const partial = tracker.onRendererCrash('oom', 1, T0 + RELOAD_WINDOW_MS + 1);
    expect(partial.budget.allowed).toBe(true);
    expect(partial.budget.recent).toHaveLength(2);
    expect(partial.notice.attempt).toBe(3);
  });

  it('窗口整体滑过之后预算完全恢复，无需显式重置', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    tracker.onRendererCrash('oom', 1, T0 + 1000);
    tracker.onRendererCrash('oom', 1, T0 + 2000);
    expect(tracker.onRendererCrash('oom', 1, T0 + 3000).budget.allowed).toBe(false);

    // 连最后一条（T0+2000）也过期
    const later = T0 + 2000 + RELOAD_WINDOW_MS + 1;
    const recovered = tracker.onRendererCrash('oom', 1, later);
    expect(recovered.budget.allowed).toBe(true);
    expect(recovered.budget.recent).toEqual([]);
    expect(recovered.notice.attempt).toBe(1);
  });

  it('崩溃不清空在飞登记表（bridge 里的 turn 可能仍在跑）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.markTurnStarted('folder:a', T0);
    tracker.onRendererCrash('oom', 1, T0 + 1000);
    expect(tracker.getInFlightSessionKeys()).toEqual(['folder:a']);
  });

  it('notice 带上崩溃原因、exitCode 与时刻', () => {
    const tracker = new CrashRecoveryTracker();
    const { notice } = tracker.onRendererCrash('oom', -536870904, T0);
    expect(notice.reason).toBe('oom');
    expect(notice.exitCode).toBe(-536870904);
    expect(notice.crashedAt).toBe(T0);
    expect(notice.id).toBe(String(T0));
  });

  it('新崩溃覆盖旧 notice（只提示最近一次）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    tracker.onRendererCrash('crashed', 2, T0 + 5000);
    expect(tracker.peekNotice(T0 + 5000)?.reason).toBe('crashed');
  });
});

describe('CrashRecoveryTracker.peekNotice — 只读、不消费', () => {
  it('重复读取返回同一条（多次挂载读到同一 notice）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    const first = tracker.peekNotice(T0 + 1000);
    const second = tracker.peekNotice(T0 + 2000);
    expect(first).not.toBeNull();
    expect(second).toEqual(first);
    // 第三次仍然读得到——消费式读取会让第一次被丢弃的结果把提示一起吃掉
    expect(tracker.peekNotice(T0 + 3000)).toEqual(first);
  });

  it('没有崩溃时返回 null', () => {
    expect(new CrashRecoveryTracker().peekNotice(T0)).toBeNull();
  });

  it('TTL 内可见，超过 TTL 视为陈旧不再下发', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    expect(tracker.peekNotice(T0 + NOTICE_TTL_MS)).not.toBeNull();
    expect(tracker.peekNotice(T0 + NOTICE_TTL_MS + 1)).toBeNull();
  });

  it('TTL 过期后再次崩溃：新 notice 覆盖陈旧值', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.onRendererCrash('oom', 1, T0);
    expect(tracker.peekNotice(T0 + NOTICE_TTL_MS + 1)).toBeNull();

    const revivedAt = T0 + NOTICE_TTL_MS + 2000;
    tracker.onRendererCrash('crashed', 2, revivedAt);
    expect(tracker.peekNotice(revivedAt)?.reason).toBe('crashed');
  });

  it('reset 清空在飞表、预算与 notice', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.markTurnStarted('folder:a', T0);
    tracker.onRendererCrash('oom', 1, T0);
    tracker.reset();
    expect(tracker.getInFlightSessionKeys()).toEqual([]);
    expect(tracker.peekNotice(T0)).toBeNull();
    expect(tracker.onRendererCrash('oom', 1, T0).notice.attempt).toBe(1);
  });
});
