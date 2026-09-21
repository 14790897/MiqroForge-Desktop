/**
 * #1034 / #1118 复审：「cache ≠ source of truth」。
 *
 * 跨会话离线事件缓存（`moduleInFlightCache`）只是**补白**：用户切回会话时，
 * `load()` 先拿 `sessions.get()` 的持久化历史当正文（`ChatConsole.tsx` 里
 * 「sessions.get() is the authoritative, deduped source — build merged FROM it」），
 * 缓存事件只在历史没有对应行时补一条。所以缓存里的 final 允许被驱逐掏空——
 * 前提是**掏空之后不能反过来变成答案**：一条空壳 final 若被当成回复渲染，
 * 用户看到的就是回答消失（或被截断的残句）而不是完整历史。
 *
 * 本文件锁两件事：
 *  1. 驱逐**真的**会把较旧的终态清成 `{_evicted:true}` 占位（生产者侧，跑真实
 *     `pushInFlightEvent` / `evictInFlightOverflow`，不是手搓占位）；
 *  2. 占位不产出答案、也不妨碍「回合已结束」的判定（消费者侧，跑真实
 *     `splitCachedMessages` 与 load() 判定 `turnDone` 用的那条规则）。
 *
 * 端到端部分（切会话 → 缓存溢出 → final → 切回）由
 * `tests/e2e/issue-1118-cross-session-replay.spec.ts` 覆盖。
 */
import { describe, expect, it } from 'vitest';
import {
  IN_FLIGHT_MAX_BYTES,
  TERMINAL_PAYLOAD_MAX_BYTES,
  capTerminalEventData,
  createInFlightSnapshot,
  pushInFlightEvent,
  sessionMsgsToUi,
  splitCachedMessages,
} from '../src/renderer/features/chat/ChatConsole';

/** 一个刚好还在 TERMINAL_PAYLOAD_MAX_BYTES 之内（capTerminalEventData 不裁剪）
 *  的 final 载荷——只有这种尺寸才能把缓冲区顶过 IN_FLIGHT_MAX_BYTES 而又
 *  不被自己在 ingest 时截断，从而让驱逐成为唯一出路。 */
const BIG_CONTENT = 'A'.repeat(520_000);

/** 跑真实的 ingest 路径：capTerminalEventData（调用点同款）→ pushInFlightEvent。 */
function pushFinal(
  snapshot: ReturnType<typeof createInFlightSnapshot>,
  content: string,
  timestamp: number
): void {
  pushInFlightEvent(snapshot, {
    type: 'final',
    data: capTerminalEventData({ content }),
    timestamp,
  });
}

describe('#1118 缓存被掏空的 final 不再充当答案（cache ≠ source of truth）', () => {
  it('生产者：字节上限被顶破时，较旧的终态被清成 {_evicted:true} 占位', () => {
    const snapshot = createInFlightSnapshot();

    // ingest 侧不裁剪 —— 断言前提，失败说明常量变了、本用例的触发量要跟着调。
    const payload = capTerminalEventData({ content: BIG_CONTENT }) as { content: string };
    expect(payload.content).toBe(BIG_CONTENT);
    expect(BIG_CONTENT.length * 2).toBeLessThan(TERMINAL_PAYLOAD_MAX_BYTES);

    pushFinal(snapshot, BIG_CONTENT, 1);
    expect(snapshot.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    // 一条还装得下 —— 此时没有任何东西被清
    expect(snapshot.events).toHaveLength(1);
    expect((snapshot.events[0].data as { content?: string }).content).toBe(BIG_CONTENT);

    // 第二条同样大的 final（新回合）把缓冲区顶过上限：没有 progress 可丢，
    // 于是走 step 2 —— 把「较旧的终态」掏空。
    pushFinal(snapshot, BIG_CONTENT, 2);

    expect(snapshot.events).toHaveLength(2);
    expect(snapshot.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    // 旧的被掏空，且是**占位形状**（不是被删掉 —— 回合仍需读得出「已结束」）
    expect(snapshot.events[0].type).toBe('final');
    expect(snapshot.events[0].data).toEqual({ _evicted: true });
    // 新的（用户正在看的那个回合）内容一字不少
    expect((snapshot.events[1].data as { content?: string }).content).toBe(BIG_CONTENT);
  });

  it('消费者：占位 final 不产出回复，也不产出「Unknown error」式空壳', () => {
    const placeholder = { type: 'final' as const, data: { _evicted: true }, timestamp: 1 };

    const split = splitCachedMessages([placeholder]);
    // 不产出 finalReply ⇒ load() 不会 push 一条助手气泡 ⇒ 屏幕上只剩历史里的完整回复
    expect(split.finalReply).toBeNull();
    // thinking 侧同样安静（占位不是 progress / error）
    expect(split.thinking).toHaveLength(0);
  });

  it('反例（非空洞）：带内容的 final 确实会产出回复', () => {
    const split = splitCachedMessages([
      { type: 'final', data: { content: '完整回复' }, timestamp: 1 },
    ]);
    expect(split.finalReply).toBe('完整回复');
  });

  it('占位仍读得出「回合已结束」——卡死思考块的守卫不依赖 final 的内容', () => {
    const snapshot = createInFlightSnapshot();
    pushFinal(snapshot, BIG_CONTENT, 1);
    pushFinal(snapshot, BIG_CONTENT, 2);

    // 这正是 load() 计算 turnDone 的那条规则（ChatConsole.tsx:
    // `const turnDone = !!cached?.events.some((e) => e.type === 'final')`）：
    // 它只看 type，不看 payload —— 所以被掏空的 final 之后，快照里那个
    // isLiveReasoning 仍然会被摘掉，「思考中…」不会永久转圈。
    expect(snapshot.events.some((e) => e.type === 'final')).toBe(true);
    expect(splitCachedMessages(snapshot.events).finalReply).toBe(BIG_CONTENT);
  });

  it('完整历史走的是另一条路：sessionMsgsToUi 原样带出后端全文', () => {
    // 与缓存无关的权威来源。缓存把内容丢光时，用户看到的仍然是这一段。
    const ui = sessionMsgsToUi([
      { role: 'assistant', content: BIG_CONTENT, timestamp: '2026-09-18T00:00:00.000Z' },
    ]);
    expect(ui).toHaveLength(1);
    expect(ui[0].role).toBe('assistant');
    expect(String(ui[0].content)).toBe(BIG_CONTENT);
  });

  it('已知缺口：被「截断」（而非清空）的 final 无法与历史精确去重', () => {
    // capTerminalEventData 对超过 TERMINAL_PAYLOAD_MAX_BYTES 的 content 走
    // step 3：留 20000 字 + '…'。这跟 stripTerminalPayload 的「清空」不同 ——
    // 截断后的 payload **仍然带 content**，于是 splitCachedMessages 会把它当答案
    // 返回，而 load() 的去重是精确匹配：
    //   merged.some((m) => m.role === 'assistant' && String(m.content) === finalContent.trim())
    // 截断文本 ≠ 历史全文 ⇒ 去重不命中 ⇒ 完整回复下面会多出一条被截断的回复气泡。
    // 走通它需要 content > ~52 万字符的 final（≈1 MB UTF-16），本用例只把
    // 「去重为何不命中」钉住；是否改成「宁可不要也不给残句」是行为决策，已在
    // 汇报里提请 review，不在本次测试补充的改动范围内。
    const full = 'x'.repeat(900_000);
    const capped = capTerminalEventData({ content: full }) as { content: string };

    expect(capped.content.length).toBe(20_001); // 20000 字 + '…'
    expect(capped.content.endsWith('…')).toBe(true);
    expect(capped.content).not.toBe(full); // ⇒ 精确匹配必然落空
    // 但它确实还带着 content —— 所以会被当成答案返回（这就是缺口所在）
    expect(splitCachedMessages([{ type: 'final', data: capped, timestamp: 1 }]).finalReply).toBe(
      capped.content
    );
  });
});
