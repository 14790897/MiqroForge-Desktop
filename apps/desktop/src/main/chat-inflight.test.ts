import { describe, expect, it, vi } from 'vitest';
import {
  abortInFlightChats,
  clearInFlightChats,
  inFlightChatSessions,
  trackInFlightChat,
  untrackInFlightChat,
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
  it('登记 / 摘除 / 清空', () => {
    clearInFlightChats();
    trackInFlightChat('desktop:a');
    trackInFlightChat('desktop:a'); // 同会话重复登记只记一次
    trackInFlightChat('desktop:b');
    expect(inFlightChatSessions().sort()).toEqual(['desktop:a', 'desktop:b']);

    untrackInFlightChat('desktop:a');
    expect(inFlightChatSessions()).toEqual(['desktop:b']);

    clearInFlightChats();
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('空串会话键不登记（拿不到会话名时不发无意义的中断）', () => {
    clearInFlightChats();
    trackInFlightChat('');
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('逐会话发 chat.abort，并在发完前清空登记', async () => {
    clearInFlightChats();
    trackInFlightChat('desktop:a');
    trackInFlightChat('desktop:b');
    const sender = makeSender();

    const aborted = await abortInFlightChats(sender);

    expect(aborted.sort()).toEqual(['desktop:a', 'desktop:b']);
    expect(sender.calls).toEqual([
      ['chat.abort', { session_key: 'desktop:a' }],
      ['chat.abort', { session_key: 'desktop:b' }],
    ]);
    // 登记已清空：残留登记会让下一次登出误伤新会话
    expect(inFlightChatSessions()).toEqual([]);
  });

  it('单个会话中断失败不影响其余会话，登记同样清空', async () => {
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

    await abortInFlightChats(sender);

    expect(calls).toEqual(['desktop:a', 'desktop:b']);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('中断在途回合失败（desktop:a）'));
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
