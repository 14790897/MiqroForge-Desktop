/**
 * #1191 回归：`sessions.list` 失败时，主进程必须把「失败」原样交给渲染层。
 *
 * 这里以前返回 `{ sessions: [] }`——与「用户确实没有会话」在渲染层不可区分，
 * 侧栏只能照单清空，用户看到的就是「聊着聊着会话全不见了」。实测那次
 * `sessions.list` 被别的请求挤到 **720s** 才超时（串行队列的排队饥饿，见 #1191），
 * 那不该是清空用户会话列表的理由。
 *
 * 渲染层那一半在同一次改动里（Sidebar.tsx 只在拿到真实列表时覆盖）；这个文件守的是
 * 中间那道接缝：`sendSafe` 返回 null 时 handler 也必须返回 null，否则渲染层再对也白搭。
 */
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { IPC } from '../../shared/ipc';

const handlers = new Map<string, (...args: unknown[]) => unknown>();
const sendSafe = vi.fn();

/** Proxy 的 prop 可能是 symbol，只有字符串键才是我们要转发的属性。 */
function inTarget(target: object, prop: string | symbol): prop is string {
  return typeof prop === 'string' && prop in target;
}

beforeAll(async () => {
  // main 进程码只从 shared/electron 取运行时，那里的模块级断言要求
  // globalThis.__ELECTRON__ 先就位。菜单/对话框这些只在 lambda 里用，给空壳即可。
  (globalThis as unknown as { __ELECTRON__: unknown }).__ELECTRON__ = new Proxy(
    {
      ipcMain: {
        handle: (channel: string, fn: (...args: unknown[]) => unknown) => handlers.set(channel, fn),
        on: (channel: string, fn: (...args: unknown[]) => unknown) => handlers.set(channel, fn),
      },
      dialog: {},
      shell: {},
      app: {},
      clipboard: {},
    },
    {
      get: (target, prop) =>
        inTarget(target, prop) ? (target as Record<string, unknown>)[prop] : {},
    }
  );

  const { registerIpcHandlers } = await import('./index');
  // 只关心 sessions:list 这一个 handler，其余注册进去就行。
  const bridge = new Proxy(
    { sendSafe },
    {
      get: (target, prop) =>
        inTarget(target, prop) ? (target as Record<string, unknown>)[prop] : () => undefined,
    }
  );
  registerIpcHandlers(bridge as never);
});

function sessionsList(): (...args: unknown[]) => unknown {
  const handler = handlers.get(IPC.SESSIONS_LIST);
  expect(handler, `${IPC.SESSIONS_LIST} 未注册`).toBeTypeOf('function');
  return handler!;
}

describe('sessions:list handler 对失败的处理', () => {
  it('把失败原样交给渲染层，而不是伪造空列表', async () => {
    sendSafe.mockResolvedValueOnce(null);

    const result = await sessionsList()({}, undefined);

    expect(sendSafe).toHaveBeenCalledWith('sessions.list');
    // 关键：不是 { sessions: [] }——渲染层要靠这个区别决定「保留」还是「清空」。
    expect(result).toBeNull();
  });

  it('真实的空列表照样透传，不能被上一条兜成 null', async () => {
    sendSafe.mockResolvedValueOnce({ sessions: [] });

    const result = await sessionsList()({}, undefined);

    expect(result).toEqual({ sessions: [] });
  });
});
