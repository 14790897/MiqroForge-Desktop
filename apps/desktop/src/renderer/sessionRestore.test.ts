/**
 * #1118：启动恢复 lastSession 的幽灵会话判定。
 *
 * 这个判定的价值在于「用户看到的是欢迎页，但当前会话 key 其实不存在」这种
 * 静默状态：bridge 的 sessions.get 对未知 key 是 get_or_create，不会报错，
 * 所以只有启动时拿 sessions.list 对照才能发现。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_SESSION_KEY,
  resolveUnverifiedRestoreKey,
  RESTORE_VERIFY_ATTEMPTS,
  shouldArmRestoreTimeout,
  shouldFallbackToDefaultSession,
  shouldVerifyRestoredSession,
  verifyRestoredSession,
} from './sessionRestore';

describe('shouldFallbackToDefaultSession', () => {
  it('会话已不存在（store 里查无此 key）→ 回退', () => {
    expect(shouldFallbackToDefaultSession('desktop:1789704154596', ['desktop:123'])).toBe(true);
  });

  it('会话仍在 → 不回退（保持恢复出来的会话）', () => {
    expect(
      shouldFallbackToDefaultSession('desktop:1789704154596', [
        'desktop:123',
        'desktop:1789704154596',
      ])
    ).toBe(false);
  });

  it('store 为空（全新 profile + 幽灵 key）→ 回退', () => {
    // 这是 #1118 第七轮 flake 的形状：共享 profile 残留上一轮的 lastSession，
    // 本轮 store 里根本没有那个会话。
    expect(shouldFallbackToDefaultSession('desktop:1789704154596', [])).toBe(true);
  });

  it('默认态哨兵不回退（它就是回退目标）', () => {
    expect(shouldFallbackToDefaultSession(DEFAULT_SESSION_KEY, [])).toBe(false);
    expect(shouldFallbackToDefaultSession(DEFAULT_SESSION_KEY, ['desktop:123'])).toBe(false);
  });

  it('读不到 lastSession（null / undefined / 空串）→ 不动', () => {
    expect(shouldFallbackToDefaultSession(null, ['desktop:123'])).toBe(false);
    expect(shouldFallbackToDefaultSession(undefined, [])).toBe(false);
    expect(shouldFallbackToDefaultSession('', [])).toBe(false);
  });

  it('自定义默认 key 时同样成立', () => {
    expect(shouldFallbackToDefaultSession('desktop:9', [], 'desktop:my-default')).toBe(true);
    expect(shouldFallbackToDefaultSession('desktop:my-default', [], 'desktop:my-default')).toBe(
      false
    );
  });
});

/**
 * 第八轮两阶段启动的门：非默认哨兵的恢复 key 必须先校验存在性，ChatConsole 才能
 * 挂载（否则它会用 get-or-create 的 sessions.get 先把幽灵 key 摸一遍——第七轮
 * E2E 实测抓到 4 次 ghost get + 1 次 ghost delete）。
 */
describe('shouldVerifyRestoredSession', () => {
  it('恢复出来的是普通会话 key → 必须先校验', () => {
    expect(shouldVerifyRestoredSession('desktop:1789704154596')).toBe(true);
    expect(shouldVerifyRestoredSession('doc:月度报告')).toBe(true);
  });

  it('默认哨兵不用校验（它就是要回退到的目标）', () => {
    expect(shouldVerifyRestoredSession(DEFAULT_SESSION_KEY)).toBe(false);
  });

  it('读不到 lastSession（null / undefined / 空串）→ 不用校验', () => {
    expect(shouldVerifyRestoredSession(null)).toBe(false);
    expect(shouldVerifyRestoredSession(undefined)).toBe(false);
    expect(shouldVerifyRestoredSession('')).toBe(false);
  });

  it('自定义默认 key 时同样成立', () => {
    expect(shouldVerifyRestoredSession('desktop:9', 'desktop:my-default')).toBe(true);
    expect(shouldVerifyRestoredSession('desktop:my-default', 'desktop:my-default')).toBe(false);
  });
});

/**
 * 第十轮 CR：兜底计时器只在**同意门开启后**武装。
 *
 * 为什么这是个真值表而不是「一行 &&」的仪式：计时器的语义是「等桥起不来就回退
 * 默认」，而桥的启动被同意门挡在后面（consent-first）。同意前武装 = 用户在同意页
 * 上读协议的时间会消耗「等桥」的预算，停留超过 RESTORE_GATE_MAX_MS 就在存在性
 * 校验**开始之前**把恢复出来的 key 判负（`openGateUnverified` 还会把
 * `restoredSessionCheckedRef` 置真 → 同意之后校验再也不跑）。
 *
 * 变异验证：把 App.tsx 的武装条件改回只看 `restorePending`（或把本函数实现改成
 * `return restorePending`）——第一例立刻变红。E2E 侧见
 * issue-1118-session-restore-race.spec.ts 的「同意页停留超过兜底预算」用例。
 */
describe('shouldArmRestoreTimeout（同意门开启前不武装兜底计时器）', () => {
  it('校验还挂着 + 同意门未过 → 不武装（核心：别拿同意页的停留时间当等桥预算）', () => {
    expect(shouldArmRestoreTimeout(true, false)).toBe(false);
  });

  it('校验还挂着 + 同意门已过 → 武装（同意后才从零开始计预算）', () => {
    expect(shouldArmRestoreTimeout(true, true)).toBe(true);
  });

  it('已有结论（哨兵/读不到 lastSession）→ 不武装，无论同意与否', () => {
    expect(shouldArmRestoreTimeout(false, true)).toBe(false);
    expect(shouldArmRestoreTimeout(false, false)).toBe(false);
  });
});

/**
 * 第九轮 CR：「验证失败 / 超时 → 绝不带着未验证的 key 挂载」的状态机。
 *
 * 快速路径（sleep 注入成 noop）保证单测不真的等退避。
 */
const noSleep = async () => {};

describe('verifyRestoredSession（有界重试后给结论）', () => {
  it('key 存在 → keep', async () => {
    const load = vi.fn(async () => ['desktop:1', 'desktop:9']);
    await expect(verifyRestoredSession('desktop:9', load, { sleep: noSleep })).resolves.toBe(
      'keep'
    );
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('明确查无此 key → fallback（幽灵）', async () => {
    const load = vi.fn(async () => ['desktop:1']);
    await expect(verifyRestoredSession('desktop:9', load, { sleep: noSleep })).resolves.toBe(
      'fallback'
    );
  });

  it('执行失败 → 重试到上限后返回 unverified（不是 fallback、更不是 keep）', async () => {
    const load = vi.fn(async () => {
      throw new Error('bridge not ready');
    });
    await expect(verifyRestoredSession('desktop:9', load, { sleep: noSleep })).resolves.toBe(
      'unverified'
    );
    expect(load).toHaveBeenCalledTimes(RESTORE_VERIFY_ATTEMPTS);
  });

  it('先失败后成功 → 立刻采信成功的那次（不把瞬时失败当结论）', async () => {
    let n = 0;
    const load = async () => {
      n += 1;
      if (n === 1) throw new Error('transient');
      return ['desktop:9'];
    };
    await expect(verifyRestoredSession('desktop:9', load, { sleep: noSleep })).resolves.toBe(
      'keep'
    );
    expect(n).toBe(2);
  });

  it('重试按 attempts 收敛（可覆盖），且退避被等待', async () => {
    const sleeps: number[] = [];
    const sleep = async (ms: number) => {
      sleeps.push(ms);
    };
    const load = vi.fn(async () => {
      throw new Error('down');
    });
    await expect(
      verifyRestoredSession('desktop:9', load, { attempts: 3, backoffMs: 250, sleep })
    ).resolves.toBe('unverified');
    expect(load).toHaveBeenCalledTimes(3);
    expect(sleeps).toEqual([250, 500]); // 次数-1 次退避
  });

  it('任何失败都不外抛（调用方不必再包 try/catch）', async () => {
    const load = async () => {
      throw new Error('boom');
    };
    await expect(
      verifyRestoredSession('desktop:9', load, { attempts: 1, sleep: noSleep })
    ).resolves.toBe('unverified');
  });

  it('默认哨兵 / 空值不校验：一次 IPC 都不发、直接 keep', async () => {
    const load = vi.fn(async () => []);
    for (const key of [DEFAULT_SESSION_KEY, null, undefined, '']) {
      await expect(verifyRestoredSession(key, load, { sleep: noSleep })).resolves.toBe('keep');
    }
    expect(load).not.toHaveBeenCalled();
  });
});

/**
 * 第九轮的**核心不变量**：没能验证时，绝不放行那个未验证的非默认 key。
 *
 * 变异验证：把 `resolveUnverifiedRestoreKey` 的兜底从 `return defaultKey` 改回
 * 「保持现状」（`return currentKey`）——本组第一个用例立刻变红（返回的正是那个
 * 未验证的幽灵 key），E2E `-g "1118"` 的 list 失败用例同时变红（ChatConsole 带着
 * 幽灵 key 挂载 → 记录器抓到幽灵 get）。改回即绿。
 */
describe('resolveUnverifiedRestoreKey（没能验证 → 显式回退默认）', () => {
  it('未验证的非默认 key → 回退默认哨兵（绝不返回它）', () => {
    const ghost = 'desktop:1789704154596';
    const next = resolveUnverifiedRestoreKey(ghost, ghost);
    expect(next).toBe(DEFAULT_SESSION_KEY);
    expect(next).not.toBe(ghost);
  });

  it('用户已经切走 → 交棒：保持用户当前的会话，不覆盖他的选择', () => {
    expect(resolveUnverifiedRestoreKey('desktop:1789704154596', 'desktop:user-picked')).toBe(
      'desktop:user-picked'
    );
  });

  it('当前已经是默认哨兵（如超时前已回退过）→ 仍是默认哨兵', () => {
    expect(resolveUnverifiedRestoreKey('desktop:1789704154596', DEFAULT_SESSION_KEY)).toBe(
      DEFAULT_SESSION_KEY
    );
  });

  it('恢复出来的本来就是默认哨兵 / 空值 → 不动', () => {
    expect(resolveUnverifiedRestoreKey(DEFAULT_SESSION_KEY, DEFAULT_SESSION_KEY)).toBe(
      DEFAULT_SESSION_KEY
    );
    expect(resolveUnverifiedRestoreKey(null, DEFAULT_SESSION_KEY)).toBe(DEFAULT_SESSION_KEY);
  });

  it('自定义默认 key 时同样成立', () => {
    expect(resolveUnverifiedRestoreKey('desktop:9', 'desktop:9', 'desktop:my-default')).toBe(
      'desktop:my-default'
    );
  });
});
