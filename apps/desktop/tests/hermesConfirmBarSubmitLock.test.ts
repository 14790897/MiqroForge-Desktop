// @vitest-environment jsdom
/**
 * #1071 G7 P1（外部评审）回归锁：确认条**失败可重试**。
 *
 * 背景：确认卡的提交锁此前只有「上锁」没有「解锁」。UserInputContext.resolve
 * 在 IPC 失败时会把卡片回滚成 pending、按钮重新可点，但 HermesConfirmBar 组件
 * 实例里的 ref/state 还锁着——`busyNow` 永久为真、按钮永久 disabled，用户失去
 * 重试路径；ActionCard 自带的那把锁更彻底：从不释放。
 *
 * 修复后契约（与 PlanCard.resolveOnce 一致）：
 *   - onResolve 返回 false（resolve 内部已回滚）→ 释放锁，按钮恢复可点；
 *   - onResolve 抛错 / rejected → 同上；
 *   - onResolve 成功（undefined / true）→ **保持**上锁到卡片消失（防重复提交）。
 *
 * 本文件是**行为测试**（jsdom + react-dom/client + act），不是源码文本锁：
 * 这三条不变量正是「点了才知道」的，必须真跑一遍点击。仓库里没有 @testing-library，
 * 故直接用 createElement + dispatchEvent；`// @vitest-environment jsdom` 逐文件
 * 覆盖 vitest 配置里的默认 node 环境（jsdom 已在 devDependencies）。
 */
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  HermesConfirmBar,
  type HermesConfirmChoice,
} from '../src/renderer/features/chat/components/HermesConfirmBar';

type ResolveFn = (
  choice: HermesConfirmChoice,
  rememberMode?: 'session' | 'always' | null
) => void | Promise<boolean | void>;

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

/** 手动可控的 Promise：测试里决定「这次提交什么时候失败」。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let container: HTMLDivElement;
let root: Root;

/** onResolve 调用次序记录（默认只关心次数与 choice）。 */
let calls: HermesConfirmChoice[];

function mountBar(onResolve: ResolveFn) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(createElement(HermesConfirmBar, { runLabel: '确认执行', onResolve }));
  });
}

function runButton(): HTMLButtonElement {
  const el = container.querySelector('[data-testid="confirm-run"]');
  if (!el) throw new Error('找不到主按钮 [data-testid="confirm-run"]');
  return el as HTMLButtonElement;
}

function click(el: HTMLElement) {
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
}

/** 让 resolveOnce 的 await 续体 + React 重渲染都落地。 */
async function settle(promise: Promise<unknown>) {
  await act(async () => {
    await promise;
    await Promise.resolve();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  calls = [];
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('HermesConfirmBar：提交失败必须解锁可重试（#1071 G7 P1）', () => {
  it('onResolve 返回 false（已回滚）：按钮恢复可点，再点能提交成功', async () => {
    const first = deferred<boolean | void>();
    const onResolve: ResolveFn = (choice) => {
      calls.push(choice);
      return calls.length === 1 ? first.promise : undefined;
    };
    mountBar(onResolve);

    click(runButton());
    expect(calls).toEqual(['confirm']);
    expect(runButton().disabled, '提交中主按钮应禁用').toBe(true);

    // 后端拒绝 → resolve 内部回滚 → 返回 false
    first.resolve(false);
    await settle(first.promise);
    expect(runButton().disabled, '失败后主按钮必须恢复可点（此前永久 disabled）').toBe(false);

    click(runButton());
    expect(calls, '用户应能重试，onResolve 被调用第二次').toEqual(['confirm', 'confirm']);
  });

  it('onResolve 抛错 / rejected：锁同样释放，可再次点击', async () => {
    const first = deferred<boolean | void>();
    const onResolve: ResolveFn = (choice) => {
      calls.push(choice);
      return calls.length === 1 ? first.promise : undefined;
    };
    mountBar(onResolve);

    click(runButton());
    expect(calls).toHaveLength(1);

    first.reject(new Error('ipc resolve failed'));
    // resolveOnce 内部 try/catch 会吃掉这个 rejection；这里再兜一层避免
    // vitest 把「同一个 promise 的消费」记成 unhandled rejection。
    await settle(first.promise.catch(() => undefined));
    expect(runButton().disabled, 'rejected 后主按钮必须恢复可点').toBe(false);

    click(runButton());
    expect(calls).toHaveLength(2);
  });

  it('成功路径（返回 undefined）：保持上锁，不退回旧的可重复提交行为', async () => {
    const first = deferred<boolean | void>();
    const onResolve: ResolveFn = (choice) => {
      calls.push(choice);
      return calls.length === 1 ? first.promise : undefined;
    };
    mountBar(onResolve);

    click(runButton());
    first.resolve(undefined);
    await settle(first.promise);

    expect(runButton().disabled, '成功路径仍应禁用（卡片即将消失）').toBe(true);
    click(runButton());
    expect(calls, '成功后再点不应产生第二次提交').toEqual(['confirm']);
  });

  it('同一 tick 连点两次：ref 同步拦截，只提交一次', () => {
    const onResolve: ResolveFn = (choice) => {
      calls.push(choice);
      return new Promise<boolean | void>(() => {}); // 永不落地，模拟慢 IPC
    };
    mountBar(onResolve);

    const button = runButton();
    act(() => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    expect(calls, '同一 tick 双击只放行一次（state 尚未重渲染，靠 ref 拦）').toEqual(['confirm']);
  });
});
