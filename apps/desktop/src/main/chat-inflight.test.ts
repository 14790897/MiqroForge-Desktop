import { describe, expect, it, vi } from 'vitest';
import {
  abortInFlightChats,
  clearInFlightChats,
  inFlightChatSessions,
  trackInFlightChat,
  type ChatAbortSender,
} from './chat-inflight';

function makeSender(): ChatAbortSender & { calls: Array<[string, unknown]> } {
  const calls: Array<[string, unknown]> = [];
  return {
    calls,
    async sendSafeWithError(method, params) {
      calls.push([method, params]);
      return { ok: true, value: { aborted: true } };
    },
  };
}

describe('chat-inflight（#1257 登出中断在途回合）', () => {
  it('登记返回注销句柄，注销后会话从登记表消失', () => {
    clearInFlightChats();
    const releaseA = trackInFlightChat('desktop:a');
    const releaseB = trackInFlightChat('desktop:b');
    expect(inFlightChatSessions().sort()).toEqual(['desktop:a', 'desktop:b']);

    releaseA();
    expect(inFlightChatSessions()).toEqual(['desktop:b']);

    releaseB();
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('注销句柄幂等：多个终态事件 + 通道异常重复调用不误摘别人', () => {
    clearInFlightChats();
    const releaseA = trackInFlightChat('desktop:a');
    const releaseB = trackInFlightChat('desktop:b');

    releaseA();
    releaseA(); // final 事件后再来一次 aborted / catch
    releaseA();
    expect(inFlightChatSessions()).toEqual(['desktop:b']);

    releaseB();
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('同一会话两条在途请求：后到那条的终态不摘掉仍在跑的前一条（#1260 回归）', () => {
    clearInFlightChats();
    // 同一会话：第二条会被后端以 TURN_IN_PROGRESS 拒掉，先到的那条仍在跑
    const releaseFirst = trackInFlightChat('desktop:default');
    const releaseSecond = trackInFlightChat('desktop:default');
    expect(inFlightChatSessions()).toEqual(['desktop:default']);

    releaseSecond(); // 被拒那条先收到终态（error）

    // 关键：仍在跑的那条还在登记里 —— 否则登出会漏掉它
    expect(inFlightChatSessions()).toEqual(['desktop:default']);

    releaseFirst();
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('空串会话键不登记（拿不到会话名时不发无意义的中断）', () => {
    clearInFlightChats();
    const release = trackInFlightChat('');
    expect(inFlightChatSessions()).toEqual([]);
    expect(() => release()).not.toThrow();
  });

  it('逐会话发 chat.abort（同一会话多条在途只中断一次），成功后摘除登记', async () => {
    clearInFlightChats();
    trackInFlightChat('desktop:a');
    trackInFlightChat('desktop:a');
    trackInFlightChat('desktop:b');
    const sender = makeSender();

    const aborted = await abortInFlightChats(sender);

    expect(aborted.sort()).toEqual(['desktop:a', 'desktop:b']);
    expect(sender.calls).toEqual([
      ['chat.abort', { session_key: 'desktop:a' }],
      ['chat.abort', { session_key: 'desktop:b' }],
    ]);
    // 中断成功后才摘除：登记已清空，不会残留到下一次登出
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('中断失败的会话登记保留，下一次登出重试成功后摘除（#1257 桥重启场景）', async () => {
    clearInFlightChats();
    trackInFlightChat('desktop:a');
    trackInFlightChat('desktop:b');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const calls: string[] = [];
    const sender: ChatAbortSender = {
      async sendSafeWithError(_method, params) {
        const key = String((params as { session_key?: string }).session_key ?? '');
        calls.push(key);
        return key === 'desktop:a'
          ? { ok: false, error: 'Bridge not running' }
          : { ok: true, value: { aborted: true } };
      },
    };

    const aborted = await abortInFlightChats(sender);

    expect(calls).toEqual(['desktop:a', 'desktop:b']);
    expect(aborted).toEqual(['desktop:b']);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('中断在途回合失败（desktop:a）'));
    // 失败的登记保留在表里 —— 下次登出还能重试，不会永久脱离跟踪
    expect(inFlightChatSessions()).toEqual(['desktop:a']);

    // 下一次登出：桥恢复后重试成功，登记摘除
    await abortInFlightChats(makeSender());
    expect(inFlightChatSessions()).toEqual([]);
    warn.mockRestore();
  });

  it('没有在途回合时不发任何请求', async () => {
    clearInFlightChats();
    const sender = makeSender();
    expect(await abortInFlightChats(sender)).toEqual([]);
    expect(sender.calls).toEqual([]);
  });
});
