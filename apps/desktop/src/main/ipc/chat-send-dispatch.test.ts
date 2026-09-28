/**
 * #1072 — `chat:send` 的派发三态出口。
 *
 * 中间那道接缝:渲染层要区分「请求从未送出」与「已送达后端但后续失败」,才能
 * 决定编辑/重试失败时可否恢复截断前的消息列表。而 `ipcMain.handle` 的拒绝经
 * Electron 序列化后只剩 message 字符串(自定义属性丢失),渲染层读不到任何机器
 * 可判定的字段 —— 所以「确定未派发」必须以**正常返回**的形式交给渲染层,并且
 * 只覆盖真正没写进 bridge 管道的那一类失败;写出之后的失败(进程退出/重启/超时/
 * 后端 error)必须继续走 reject,否则渲染层会误以为可以回滚。
 *
 * 夹具与 sessions-list.test.ts 相同:在 main 进程码导入前用 Proxy 占位
 * globalThis.__ELECTRON__,把 ipcMain 注册的 handler 收进 Map。
 */
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { IPC, isChatNotDispatched } from '../../shared/ipc';

const handlers = new Map<string, (...args: unknown[]) => unknown>();
const bridgeSend = vi.fn();

/** Proxy 的 prop 可能是 symbol,只有字符串键才是我们要转发的属性。 */
function inTarget(target: object, prop: string | symbol): prop is string {
  return typeof prop === 'string' && prop in target;
}

beforeAll(async () => {
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
  const bridge = new Proxy(
    { send: bridgeSend },
    {
      get: (target, prop) =>
        inTarget(target, prop) ? (target as Record<string, unknown>)[prop] : () => undefined,
    }
  );
  registerIpcHandlers(bridge as never);
});

function chatSend(): (...args: unknown[]) => Promise<unknown> {
  const handler = handlers.get(IPC.CHAT_SEND);
  expect(handler, `${IPC.CHAT_SEND} 未注册`).toBeTypeOf('function');
  return handler as (...args: unknown[]) => Promise<unknown>;
}

/** 事件对象只用到 sender,而本文件里的分支都不会真的往渲染层发事件。 */
function invoke(payload: unknown): Promise<unknown> {
  return chatSend()({ sender: {} }, payload);
}

describe('chat:send 的派发三态(#1072)', () => {
  it('bridge 未运行(RequestNotDispatchedError)→ 正常返回「确定未派发」标记', async () => {
    const { RequestNotDispatchedError } = await import('../bridge');
    bridgeSend.mockRejectedValueOnce(new RequestNotDispatchedError('Bridge not running'));

    const result = await invoke({ content: 'hi', session_key: 'desktop:1' });

    expect(isChatNotDispatched(result)).toBe(true);
    expect((result as { message: string }).message).toBe('Bridge not running');
  });

  it('请求已写出后的失败 → 照旧 reject,不得伪装成「未派发」', async () => {
    bridgeSend.mockRejectedValueOnce(new Error('Bridge stopped — request cancelled'));

    await expect(invoke({ content: 'hi', session_key: 'desktop:1' })).rejects.toThrow(
      'Bridge stopped — request cancelled'
    );
  });

  it('参数构造失败(mode 非法)→ 请求从未离开本进程,按未派发返回', async () => {
    const result = await invoke({ content: 'hi', mode: 'not-a-mode' });

    expect(isChatNotDispatched(result)).toBe(true);
    // 参数没过校验就不该走到 bridge —— 否则等于把「没发出去」说成「发出去了」
    expect(bridgeSend).not.toHaveBeenCalled();
  });

  it('正常终态结果原样透传', async () => {
    bridgeSend.mockResolvedValueOnce({ message: 'done' });

    const result = await invoke({ content: 'hi', session_key: 'desktop:1' });

    expect(result).toEqual({ message: 'done' });
    expect(isChatNotDispatched(result)).toBe(false);
  });
});
