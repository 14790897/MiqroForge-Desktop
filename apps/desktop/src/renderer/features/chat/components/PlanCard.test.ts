// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { PlanCard, type PlanCardEntry } from './PlanCard';

function entry(overrides: Partial<PlanCardEntry> = {}): PlanCardEntry {
  return {
    title: '生成 MOF 实验报告',
    goal: '整理 5 篇论文并生成 Workflow',
    steps: [
      { name: '论文检索', tools: ['web_search'] },
      { name: '生成报告', tools: ['write_file'] },
      { name: '上传 MiqroForge', tools: ['upload'] },
    ],
    permissions: ['network_read', 'workspace_write', 'external_upload'],
    phase: 'wait_confirm',
    ...overrides,
  };
}

function render(e: PlanCardEntry): string {
  return renderToStaticMarkup(createElement(PlanCard, { entry: e, onResolve: () => {} }));
}

describe('PlanCard (#646-v2)', () => {
  it('wait_confirm: renders a lightweight workstream with decision actions', () => {
    const html = render(entry());
    expect(html).toContain('生成 MOF 实验报告');
    expect(html).toContain('论文检索');
    expect(html).toContain('生成报告');
    expect(html).toContain('涉及');
    expect(html).toContain('网络');
    expect(html).toContain('外部');
    expect(html).toContain('按当前方案执行');
    expect(html).toContain('调整方案');
    expect(html).toContain('取消');
  });

  it('running: shows step progress without decision controls', () => {
    const e = entry({ phase: 'running', stepStatus: { 论文检索: 'done', 生成报告: 'running' } });
    const html = render(e);
    expect(html).toContain('执行中');
    expect(html).toContain('论文检索');
    expect(html).not.toContain('按当前方案执行');
    expect(html).not.toContain('调整方案');
    expect(html).not.toContain('涉及');
    expect(html).not.toContain('网络');
  });

  it('completed / cancelled: summary states remain compact', () => {
    expect(render(entry({ phase: 'completed' }))).toContain('已完成');
    expect(render(entry({ phase: 'cancelled' }))).toContain('已取消');
  });
});

// #1071 R3（CR item 9）：提交锁——双击只 resolve 一次。
// 上面用的是 renderToStaticMarkup（只能看首屏），锁必须在真实点击下验证，
// 所以这一组挂到 jsdom 上（jsdom 是 apps/desktop 已声明的 devDependency），
// 直接派发 MouseEvent 走 React 的合成事件。
describe('PlanCard 提交锁（#1071 R3, CR item 9）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
      true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(
    e: PlanCardEntry,
    onResolve: (choiceId?: string, choiceLabel?: string) => void | Promise<boolean | void>
  ): void {
    act(() => {
      root.render(createElement(PlanCard, { entry: e, onResolve }));
    });
  }

  function el(testid: string): HTMLButtonElement {
    const node = container.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`);
    if (!node) throw new Error(`找不到元素 [data-testid="${testid}"]`);
    return node;
  }

  /** 同一 tick 内连发两次 click——React 还没重渲染时最容易漏判的时序。 */
  function doubleClick(node: Element): void {
    const click = () =>
      node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    click();
    click();
  }

  it('双击「按当前方案执行」只触发一次 onResolve', () => {
    const onResolve = vi.fn();
    mount(entry(), onResolve);

    act(() => doubleClick(el('plan-confirm')));

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve).toHaveBeenCalledWith('confirm', '按当前方案执行');
  });

  it('双击「取消」只触发一次 onResolve', () => {
    const onResolve = vi.fn();
    mount(entry(), onResolve);

    act(() => doubleClick(el('plan-cancel')));

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve).toHaveBeenCalledWith('cancel', '取消任务');
  });

  it('上锁后三个按钮都 disabled，再点也不再 resolve', () => {
    const onResolve = vi.fn();
    mount(entry(), onResolve);

    expect(el('plan-confirm').disabled).toBe(false);

    act(() => el('plan-confirm').dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(el('plan-confirm').disabled).toBe(true);
    expect(el('plan-modify').disabled).toBe(true);
    expect(el('plan-cancel').disabled).toBe(true);

    act(() => doubleClick(el('plan-cancel')));
    expect(onResolve).toHaveBeenCalledTimes(1);
  });

  it('「调整方案」重复点击无副作用；提交调整也只 resolve 一次', () => {
    const onResolve = vi.fn();
    mount(entry(), onResolve);

    act(() => doubleClick(el('plan-modify')));
    expect(onResolve).not.toHaveBeenCalled();
    expect(container.querySelectorAll('[data-testid="plan-adjustment-input"]')).toHaveLength(1);

    const textarea = container.querySelector<HTMLTextAreaElement>(
      '[data-testid="plan-adjustment-input"]'
    )!;
    act(() => {
      // React 受控 textarea：走原生 value setter + input 事件，绕过 value tracker。
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(textarea, '不要上传 Qraft');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });

    expect(el('plan-submit-adjustment').disabled).toBe(false);
    act(() => doubleClick(el('plan-submit-adjustment')));

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve).toHaveBeenCalledWith('modify', '不要上传 Qraft');
  });

  it('非 wait_confirm 阶段没有确认按钮（锁不影响其他 phase）', () => {
    mount(entry({ phase: 'running' }), vi.fn());
    expect(container.querySelector('[data-testid="plan-confirm"]')).toBeNull();
  });
});

// #1071 S5a（终审 F1）：锁必须能释放。
// 失败时 UserInputContext.resolve 会把卡片回滚成 pending，此时按钮必须恢复
// 可点，否则三个按钮永久 disabled、用户失去重试路径。两种失败形态都测：
// ① onResolve reject；② onResolve 正常返回 false（resolve 内部回滚的约定）。
describe('PlanCard 提交锁失败释放（#1071 S5a, 终审 F1）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
      true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(
    e: PlanCardEntry,
    onResolve: (choiceId?: string, choiceLabel?: string) => void | Promise<boolean | void>
  ): void {
    act(() => {
      root.render(createElement(PlanCard, { entry: e, onResolve }));
    });
  }

  function el(testid: string): HTMLButtonElement {
    const node = container.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`);
    if (!node) throw new Error(`找不到元素 [data-testid="${testid}"]`);
    return node;
  }

  async function clickOnce(testid: string): Promise<void> {
    await act(async () => {
      el(testid).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
  }

  function allWaitingButtons(): HTMLButtonElement[] {
    return [el('plan-confirm'), el('plan-modify'), el('plan-cancel')];
  }

  it('onResolve reject → 解锁，三按钮恢复 enabled，且能再次 resolve（重试打通）', async () => {
    const onResolve = vi
      .fn()
      .mockRejectedValueOnce(new Error('IPC resolve 失败'))
      .mockResolvedValueOnce(undefined);
    mount(entry(), onResolve);

    await clickOnce('plan-confirm');

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(allWaitingButtons().map((b) => b.disabled)).toEqual([false, false, false]);

    // 重试路径：再点一次要真的能再发一次 resolve，而不是被死锁吞掉。
    await clickOnce('plan-confirm');
    expect(onResolve).toHaveBeenCalledTimes(2);
    expect(onResolve).toHaveBeenLastCalledWith('confirm', '按当前方案执行');
  });

  it('onResolve 返回 false（UserInputContext 回滚约定）→ 同样解锁', async () => {
    const onResolve = vi.fn().mockResolvedValue(false);
    mount(entry(), onResolve);

    await clickOnce('plan-cancel');

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(allWaitingButtons().map((b) => b.disabled)).toEqual([false, false, false]);
  });

  it('onResolve 成功（undefined）→ 保持上锁，不因解锁逻辑放松防重复', async () => {
    const onResolve = vi.fn().mockResolvedValue(undefined);
    mount(entry(), onResolve);

    await clickOnce('plan-confirm');

    expect(allWaitingButtons().map((b) => b.disabled)).toEqual([true, true, true]);
    await clickOnce('plan-cancel');
    expect(onResolve).toHaveBeenCalledTimes(1);
  });
});
