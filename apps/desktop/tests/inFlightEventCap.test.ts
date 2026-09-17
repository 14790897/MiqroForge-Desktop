/**
 * #1034 在途事件缓存（moduleInFlightCache）计数 + 累计字节双上限（RED→GREEN）。
 *
 * 该缓存只在用户切走后接管流事件（切回时回放）。原来 `buf.events.push(...)`
 * 无上界：一个长时间思考的会话在后台可以堆下十万级事件对象，且 20 个会话
 * 各有一份。这里锁死：连续同流 delta 先折叠（**无损**，回放端按顺序拼接
 * delta，见 ChatConsole 的 exec 输出回放），折叠后仍超限则按序回收——先驱逐
 * 最旧的 progress，再掏空较旧终态的 payload（保留 type/timestamp，回放的
 * 终态判定与 watchdog 不受影响；掏空救不回预算时不掏）。最新事件永不驱逐。
 */
import { describe, expect, it } from 'vitest';
import {
  IN_FLIGHT_MAX_BYTES,
  IN_FLIGHT_MAX_EVENTS,
  IN_FLIGHT_MAX_EVENT_BYTES,
  MAX_LIVE_REASONING_CHARS,
  TERMINAL_PAYLOAD_MAX_BYTES,
  capTerminalEventData,
  capTerminalReasoning,
  createInFlightSnapshot,
  inFlightEventBytes,
  pushInFlightEvent,
} from '../src/renderer/features/chat/ChatConsole';

type Ev = Parameters<typeof pushInFlightEvent>[1];

function progress(delta: string, stream = 'stdout', callId = 'c1', timestamp = 1): Ev {
  return { type: 'progress', data: { stream, delta, tool_call_id: callId }, timestamp } as Ev;
}

function docProgress(file: string, timestamp = 1): Ev {
  return {
    type: 'progress',
    data: { type: 'doc_progress', file, stage: 'ready' },
    timestamp,
  } as Ev;
}

function terminalBrief(type: 'final' | 'error' | 'aborted', timestamp = 9): Ev {
  return { type, data: { content: 'ok' }, timestamp } as Ev;
}

/** 深层层载荷：`arguments` 落在 `data.tool_calls[].function.arguments`（depth≥3），
 *  旧限深 2 的 walker 在这里记 0。 */
function deepToolCallProgress(args: string, timestamp = 1): Ev {
  return {
    type: 'progress',
    data: { tool_calls: [{ function: { name: 'write_file', arguments: args } }] },
    timestamp,
  } as Ev;
}

/** 终态事件：`reasoning` 是 #1034 实测无界增长的那个字段。 */
function finalWithReasoning(reasoning: string, content = 'ok', timestamp = 9): Ev {
  return { type: 'final', data: { content, reasoning }, timestamp } as Ev;
}

/** 未超预算的「大」载荷（约 600 KiB）：单条合法，两条相加必然超限。 */
function bigPayload(text: string): { content: string } {
  return { content: text };
}

/** payloadBytes 的口径（见 ChatConsole：对象走 JSON.stringify，2 字节/字符）。 */
function jsonBytes(value: unknown): number {
  return JSON.stringify(value).length * 2;
}

/** 占位载荷的判定（与 ChatConsole 的 isStrippedTerminal 同一形状规则）：
 *  只剩 `_evicted`，或 `_evicted` + 至多 200 字的 message。 */
function isStrippedPayload(data: unknown): boolean {
  const record = data as { _evicted?: boolean; message?: unknown } | null | undefined;
  if (record?._evicted !== true) return false;
  const keys = Object.keys(record);
  return (
    keys.length === 1 ||
    (keys.length === 2 && typeof record.message === 'string' && record.message.length <= 200)
  );
}

/** 未封顶的 progress：大字节藏在 `delta` **之外**的字段里，按字节拆分救不了
 *  ——这是单事件上限唯一覆盖不到的形状（拆分只能切 `delta`），如实单独锁住。 */
function fatFieldProgress(payload: string, timestamp = 1): Ev {
  return {
    type: 'progress',
    data: { stream: 'stdout', delta: '', tool_call_id: 'c1', tool_output: payload },
    timestamp,
  } as Ev;
}

/** 2000 个约 1 KiB 的 key、value 全是数字：没有任何字符串可供裁剪，只能退化到
 *  有界的类型/长度摘要（`boundedTerminalSummary`）。 */
function bigKeyPayload(): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  for (let i = 0; i < 2000; i += 1) {
    data[`${i}`.padStart(6, '0') + 'k'.repeat(1024)] = i;
  }
  return data;
}

/** 回放端就是这么还原文本的：按顺序拼接同流同 tool_call_id 的 delta。 */
function concatDeltas(
  buf: ReturnType<typeof createInFlightSnapshot>,
  stream = 'stdout',
  callId = 'c1'
): string {
  let text = '';
  for (const e of buf.events) {
    if (e.type !== 'progress') continue;
    const d = e.data as { delta?: string; stream?: string; tool_call_id?: string };
    if (d.stream !== stream || d.tool_call_id !== callId) continue;
    text += d.delta ?? '';
  }
  return text;
}

/** 文本里是否存在孤立代理（高位后面不跟低位，或低位前面没有高位）。 */
function hasLoneSurrogate(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      i += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

describe('#1034 在途事件缓存上限', () => {
  it('连续同流 delta 折叠成一条，字节无损', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress('a', 'stdout', 'c1', 1));
    pushInFlightEvent(buf, progress('b', 'stdout', 'c1', 2));
    pushInFlightEvent(buf, progress('c', 'stdout', 'c1', 3));
    expect(buf.events.length).toBe(1);
    expect((buf.events[0].data as { delta: string }).delta).toBe('abc');
    expect(buf.events[0].timestamp).toBe(3); // 最新时间戳，watchdog 判活要靠它
  });

  it('不同流、以及不相邻的同流事件不折叠（顺序即语义）', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress('a', 'stdout'));
    pushInFlightEvent(buf, progress('b', 'stderr'));
    pushInFlightEvent(buf, progress('c', 'stdout'));
    expect(buf.events.length).toBe(3);
    expect(buf.events.map((e) => (e.data as { delta: string }).delta)).toEqual(['a', 'b', 'c']);
  });

  it('同一流但不同 tool_call_id 的相邻 delta 不折叠（回放归属不同）', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress('a', 'stdout', 'c1'));
    pushInFlightEvent(buf, progress('b', 'stdout', 'c2'));
    expect(buf.events.length).toBe(2);
    expect(buf.events.map((e) => (e.data as { delta: string }).delta)).toEqual(['a', 'b']);
    expect(
      buf.events.map((e) => (e.data as { delta: string; tool_call_id: string }).tool_call_id)
    ).toEqual(['c1', 'c2']);
  });

  it('没有 delta 的 progress（如 doc_progress）不参与折叠', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, docProgress('a.docx'));
    pushInFlightEvent(buf, docProgress('b.docx'));
    expect(buf.events.length).toBe(2);
  });

  it('终态事件既不折叠进前一条，也不吞掉后到的事件', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress('a'));
    pushInFlightEvent(buf, terminalBrief('final'));
    pushInFlightEvent(buf, progress('b'));
    expect(buf.events.map((e) => e.type)).toEqual(['progress', 'final', 'progress']);
  });

  it('事件数封顶，淘汰最旧的 progress、保留最新', () => {
    const buf = createInFlightSnapshot();
    // 交替流 → 每条都是独立事件，绕过折叠
    for (let i = 0; i < IN_FLIGHT_MAX_EVENTS * 3; i += 1) {
      const stream = i % 2 === 0 ? 'stdout' : 'stderr';
      pushInFlightEvent(buf, progress(`d${i}`, stream));
    }
    expect(buf.events.length).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENTS);
    const last = buf.events[buf.events.length - 1].data as { delta: string };
    expect(last.delta).toBe(`d${IN_FLIGHT_MAX_EVENTS * 3 - 1}`);
    const first = buf.events[0].data as { delta: string };
    expect(first.delta).not.toBe('d0');
  });

  it('终态事件永不被驱逐：先到的 final 在洪流之后仍在', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, terminalBrief('final'));
    for (let i = 0; i < IN_FLIGHT_MAX_EVENTS * 3 + 50; i += 1) {
      pushInFlightEvent(buf, progress(`d${i}`, i % 2 === 0 ? 'stdout' : 'stderr'));
    }
    expect(buf.events.some((e) => e.type === 'final')).toBe(true);
    expect(buf.events.length).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENTS);
  });

  it('累计字节封顶，且 bytes 记账与事件内容守恒', () => {
    const buf = createInFlightSnapshot();
    const big = 'z'.repeat(16 * 1024); // 32 KiB/条
    for (let i = 0; i < 100; i += 1) {
      pushInFlightEvent(buf, progress(big, i % 2 === 0 ? 'stdout' : 'stderr'));
    }
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  });

  it('单条 delta 逼近字节上限时被切成多条，不再出现超限巨事件', () => {
    const buf = createInFlightSnapshot();
    const half = 'q'.repeat(IN_FLIGHT_MAX_BYTES / 2 - 256); // ≈512k 字符 ≈1 MiB 字节
    pushInFlightEvent(buf, progress(half));
    pushInFlightEvent(buf, progress(half));
    expect(buf.events.length).toBeGreaterThan(2);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    for (const e of buf.events) {
      expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    }
    // 驱逐按最旧优先：存活的 chunk 合起来仍是第二个 delta 的后缀，结尾一字不差
    const kept = concatDeltas(buf);
    expect(half.endsWith(kept)).toBe(true);
    expect(kept.length).toBeGreaterThan(0);
  });

  it('单流连续 merge 把记账总量推过字节上限时，合并路径同样触发回收', () => {
    const buf = createInFlightSnapshot();
    // 填到贴着预算：16 条互不合并（各自不同的 stream）、各自贴着单事件上限的
    // progress 事件。它们是"倒数第二条及更早"，因此都可被驱逐。
    const filler = 'x'.repeat(Math.floor(IN_FLIGHT_MAX_EVENT_BYTES / 2) - 256);
    for (let i = 0; i < 16; i += 1) {
      pushInFlightEvent(buf, progress(filler, `s${i}`, `c${i}`));
    }
    // 末尾一条很小：合并只发生在它身上，合并后单条仍低于单事件上限（不走拒绝分支）
    pushInFlightEvent(buf, progress('L'.repeat(2000), 'stail', 'ctail'));
    expect(buf.events.length).toBe(17);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    const before = buf.bytes;
    const tailBefore = buf.events[buf.events.length - 1];
    const mergedBytes = inFlightEventBytes({
      type: 'progress',
      data: { stream: 'stail', delta: 'L'.repeat(2000) + 'y'.repeat(20000), tool_call_id: 'ctail' },
      timestamp: 1,
    } as Ev);
    // 前提：合并本身合法（单条不越界），但记账总量必然越界 —— 只能靠合并路径
    // 上的回收把总量拉回来（这正是 CR 复审发现的那条分支）。
    expect(mergedBytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    expect(before - inFlightEventBytes(tailBefore) + mergedBytes).toBeGreaterThan(
      IN_FLIGHT_MAX_BYTES
    );

    pushInFlightEvent(buf, progress('y'.repeat(20000), 'stail', 'ctail'));

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(16); // 最旧的 progress（填充事件）被驱逐
    expect((buf.events[buf.events.length - 1].data as { delta: string }).delta.length).toBe(22000);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  });
});

describe('#1034 复审 P1：单条 progress 事件硬上限（按字节拆分 delta）', () => {
  it('2 MiB delta 被拆成多条事件，每条都在单事件上限内，快照守住总上限', () => {
    const original = 'A'.repeat(2 * 1024 * 1024);
    expect(inFlightEventBytes(progress(original))).toBeGreaterThan(IN_FLIGHT_MAX_EVENT_BYTES);

    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress(original));

    expect(buf.events.length).toBeGreaterThan(1);
    for (const e of buf.events) {
      expect(e.type).toBe('progress');
      expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    }
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    // 驱逐只从最旧的 progress 开始 ⇒ 存活的 chunk 一定是原 delta 的一段**后缀**
    const kept = concatDeltas(buf);
    expect(kept.length).toBeGreaterThan(0);
    expect(original.endsWith(kept)).toBe(true);
  });

  it('未触发驱逐时，chunk 拼接回来与原始 delta 完全一致（回放无损）', () => {
    const original = `HEAD${'b'.repeat(200 * 1024)}TAIL`;
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress(original));
    expect(buf.events.length).toBeGreaterThan(1);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(concatDeltas(buf)).toBe(original);
  });

  it('拆分点不落在代理对中间（不留孤立半代理）', () => {
    const original = '😀'.repeat(60 * 1024);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress(original));
    expect(buf.events.length).toBeGreaterThan(1);
    for (const e of buf.events) {
      expect(hasLoneSurrogate((e.data as { delta: string }).delta)).toBe(false);
    }
    expect(concatDeltas(buf)).toBe(original);
  });

  it('拆出的 chunk 仍带原 stream/tool_call_id，后续同流 delta 仍能与之合并', () => {
    const original = 'd'.repeat(300 * 1024);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, progress(original));
    const before = buf.events.length;
    const tail = buf.events[before - 1];
    const tailDelta = (tail.data as { delta: string }).delta;
    // 末尾 chunk 还能再装多少字符（合并后的单条仍在单事件上限内）
    const room = Math.floor((IN_FLIGHT_MAX_EVENT_BYTES - inFlightEventBytes(tail)) / 2) - 16;
    expect(room).toBeGreaterThan(0);

    pushInFlightEvent(buf, progress('e'.repeat(room)));

    expect(buf.events.length).toBe(before); // 合并发生，没有新增事件
    expect((buf.events[before - 1].data as { delta: string }).delta).toBe(
      tailDelta + 'e'.repeat(room)
    );
    expect(concatDeltas(buf)).toBe(original + 'e'.repeat(room));
  });

  it('合并后也不能突破单事件上限（超限的合并被拒绝，后者独立成条）', () => {
    const buf = createInFlightSnapshot();
    const big = 'x'.repeat(Math.floor(IN_FLIGHT_MAX_EVENT_BYTES / 2) - 256);
    pushInFlightEvent(buf, progress(big));
    pushInFlightEvent(buf, progress(big));
    expect(buf.events.length).toBe(2);
    expect((buf.events[1].data as { delta: string }).delta).toBe(big);
    for (const e of buf.events) {
      expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    }
  });

  it('终态事件不参与拆分：单事件上限只约束 progress（终态由 payload cap 管）', () => {
    const buf = createInFlightSnapshot();
    const finalData = capTerminalEventData(bigPayload('f'.repeat(300 * 1024)));
    pushInFlightEvent(buf, { type: 'final', data: finalData, timestamp: 9 } as Ev);
    expect(buf.events.length).toBe(1);
    // 终态可以大于单事件上限（答案不能被切片还原），但仍在终态预算内。
    expect(inFlightEventBytes(buf.events[0])).toBeLessThanOrEqual(
      TERMINAL_PAYLOAD_MAX_BYTES + 128 + 1024
    );
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
  });
});

describe('#1034 复审 P1-a/P1-b/P2：终态尾窗 + 全深度字节计费', () => {
  it('P1-b：深层载荷（tool_calls[].function.arguments）计入字节账', () => {
    const args = 'a'.repeat(64 * 1024);
    // 旧 walker 在 depth>=2 直接返回 0 —— 这条 64 KiB 的 arguments 只记 ~128 字节，
    // 于是任何深埋的大值都能躲过 1MiB 预算。全深度计费后至少 64Ki 字符 × 2 字节。
    expect(inFlightEventBytes(deepToolCallProgress(args))).toBeGreaterThanOrEqual(64 * 1024 * 2);
  });

  it('P1-a：2MiB reasoning 的 final 入库后不击穿字节上限（截尾 + 省略标记）', () => {
    const full = 'r'.repeat(2 * 1024 * 1024);
    // 未截断的终态事件本身就是超限单条（终态不可驱逐）——这正是复审 P1-a。
    expect(inFlightEventBytes(finalWithReasoning(full))).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);

    const capped = capTerminalEventData({ content: 'ok', reasoning: full });
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: capped, timestamp: 9 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    const stored = buf.events[0].data as { content: string; reasoning: string };
    // 尾窗口径：省略头部长度的标记 + 尾部 LIVE_REASONING_KEEP_CHARS(6000) 字符。
    const omitted = full.length - 6000;
    const placeholder = `…已省略 ${omitted} 字\n\n`;
    expect(stored.reasoning.length).toBeLessThanOrEqual(
      MAX_LIVE_REASONING_CHARS + placeholder.length
    );
    expect(stored.reasoning.startsWith('…已省略 ')).toBe(true);
    expect(stored.reasoning.endsWith(full.slice(-16))).toBe(true);
    expect(stored.reasoning).toBe(placeholder + full.slice(omitted));
    expect(stored.content).toBe('ok'); // 用户可见答案原样保留
  });

  it('P1：2MiB content 的 final 入库后不击穿字节上限', () => {
    const full = 'x'.repeat(2 * 1024 * 1024);
    expect(
      inFlightEventBytes({ type: 'final', data: { content: full }, timestamp: 9 } as Ev)
    ).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);

    const capped = capTerminalEventData({ content: full });
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: capped, timestamp: 9 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    const stored = buf.events[0].data as { content: string };
    expect(buf.events[0].type).toBe('final');
    expect(stored.content.length).toBeGreaterThan(0);
    expect(stored.content).not.toBe(full);
  });

  it('P1：1.5MiB message 的 error 入库后不击穿字节上限', () => {
    const full = 'm'.repeat(Math.ceil(1.5 * 1024 * 1024));
    expect(
      inFlightEventBytes({ type: 'error', data: { message: full }, timestamp: 9 } as Ev)
    ).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);

    const capped = capTerminalEventData({ message: full });
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'error', data: capped, timestamp: 9 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    const stored = buf.events[0].data as { message: string };
    expect(buf.events[0].type).toBe('error');
    expect(stored.message.length).toBeGreaterThan(0);
    expect(stored.message).not.toBe(full);
  });

  it('P1：超大 tool_calls.arguments 的 final 入库后不击穿字节上限', () => {
    const hugeArgs = 'a'.repeat(64 * 1024);
    const toolCalls = Array.from({ length: 250 }, (_, i) => ({
      function: { name: `tool_${i}`, arguments: hugeArgs },
    }));
    const data = { tool_calls: toolCalls };
    expect(inFlightEventBytes({ type: 'final', data, timestamp: 9 } as Ev)).toBeGreaterThan(
      IN_FLIGHT_MAX_BYTES
    );

    const capped = capTerminalEventData(data);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: capped, timestamp: 9 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    const stored = buf.events[0].data as { tool_calls: unknown[] };
    expect(buf.events[0].type).toBe('final');
    // 列表被裁成前缀，但**类型仍是数组**（协议形状，见本文件的 active 用例）。
    expect(Array.isArray(stored.tool_calls)).toBe(true);
    expect(stored.tool_calls.length).toBeGreaterThan(0);
    expect(stored.tool_calls.length).toBeLessThan(250);
    const first = stored.tool_calls[0] as { function: { name: string; arguments: string } };
    expect(first.function.name).toBe('tool_0');
    expect(first.function.arguments.length).toBeLessThan(hugeArgs.length);
  });

  it('P2（二轮）：终态与 live 同一尾窗口径；短串/缺字段/非 reasoning 载荷原样透传', () => {
    expect(capTerminalReasoning('short')).toBe('short');
    expect(capTerminalReasoning(undefined)).toBeUndefined();
    expect(capTerminalReasoning('x'.repeat(MAX_LIVE_REASONING_CHARS))).toBe(
      'x'.repeat(MAX_LIVE_REASONING_CHARS)
    );

    const long = 'x'.repeat(8001);
    const capped = capTerminalReasoning(long);
    expect(capped).toBe(`…已省略 ${8001 - 6000} 字\n\n` + long.slice(8001 - 6000));
    expect(capped!.startsWith('…已省略 ')).toBe(true);
    expect(capped!.endsWith(long.slice(-6000))).toBe(true);

    // error/aborted 载荷没有 reasoning：必须原样透传（回放要靠这些字段判定会话终态）。
    const errData = { message: 'boom', code: 'E1' };
    expect(capTerminalEventData(errData)).toBe(errData);
    const shortFinal = { content: 'ok', reasoning: 'short' };
    expect(capTerminalEventData(shortFinal)).toBe(shortFinal);
  });
});

describe('#1034 复审二轮：terminal payload 递归硬上限 + 多终态快照驱逐', () => {
  it('P1：嵌套 object 里的巨型字符串同样被硬上限拦住', () => {
    const nested = { metadata: { details: { hugeText: 'x'.repeat(2 * 1024 * 1024) } } };
    // 旧 fallback 只看顶层字符串字段，这条 payload 一个字节都不会被裁。
    expect(jsonBytes(nested)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(nested);

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: capped, timestamp: 9 } as Ev);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);

    // 树形与短字段保留，只有超长的那条字符串被裁到上限内。
    const details = (capped as { metadata: { details: { hugeText: string } } }).metadata.details;
    expect(typeof details.hugeText).toBe('string');
    expect(details.hugeText.length).toBeLessThan(2 * 1024 * 1024);
    expect(details.hugeText.endsWith('…')).toBe(true);
    // 复制写：调用方的原对象不被就地改写。
    expect(nested.metadata.details.hugeText.length).toBe(2 * 1024 * 1024);
  });

  it('P1：数组元素里的深层字符串同样计入并裁剪', () => {
    const data = { events: [{ payload: [{ blob: 'y'.repeat(1024 * 1024) }] }], note: 'short' };
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as {
      events: { payload: { blob: string }[] }[];
      note: string;
    };

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(capped.note).toBe('short');
    expect(capped.events[0].payload[0].blob.length).toBeLessThan(1024 * 1024);
    expect(capped.events[0].payload[0].blob.endsWith('…')).toBe(true);
  });

  it('P1：字符串裁无可裁（字节藏在 key 里）时退化为有界摘要', () => {
    // 2000 个约 1 KiB 的 key、全部是数字 value：没有任何字符串可供裁剪，
    // 只能走最后的类型/长度摘要，且结果必须仍在预算内。
    const data: Record<string, unknown> = {};
    for (let i = 0; i < 2000; i += 1) data[`${i}`.padStart(6, '0') + 'k'.repeat(1024)] = i;
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as Record<string, unknown>;

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(capped._truncated).toBe(true);
    expect(typeof capped.size).toBe('number');
  });

  it('P1：病态深嵌套不抛异常，退化为可序列化的有界摘要', () => {
    // 2 MiB 的浅层字符串让预算判断读到超限，20 万层的深树让递归 walker 爆栈：
    // 任何异常都不许逃进 ingest 路径，结果必须是有界的浅结构。
    let deep: Record<string, unknown> = { end: true };
    for (let i = 0; i < 200000; i += 1) deep = { next: deep };
    const data = { blob: 'x'.repeat(2 * 1024 * 1024), deep };
    // 前提：这棵树连 JSON.stringify 都吃不下（payloadBytes 走兜底 walker）。
    expect(() => JSON.stringify(data)).toThrow();

    let capped: unknown;
    expect(() => {
      capped = capTerminalEventData(data);
    }).not.toThrow();

    expect(() => JSON.stringify(capped)).not.toThrow();
    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect((capped as { _truncated?: boolean })._truncated).toBe(true);
  });

  it('P1：裁剪点不落在代理对中间（不留孤立半代理）', () => {
    // 超预算两倍以上时 take 撞上 floor(len/2)，裁剪点会落在奇数下标——
    // 不做对齐就会在省略号前留下一个孤立高位代理（渲染成 U+FFFD）。
    const text = '😀'.repeat(1000000) + 'xx';
    expect(jsonBytes({ text })).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData({ text }) as { text: string };
    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    const body = capped.text.endsWith('…') ? capped.text.slice(0, -1) : capped.text;
    expect(hasLoneSurrogate(body)).toBe(false);
  });

  it('P1：退化摘要仍保留字段类型（content/message 仍是字符串）', () => {
    // 摘要路径是最后手段：字节都藏在 key 里时，用户可见的 content 不能因为
    // 退化成描述对象而在回放时变成空答案。
    const data: Record<string, unknown> = { content: 'c'.repeat(300 * 1024), type: 'final' };
    for (let i = 0; i < 2000; i += 1) data[`${i}`.padStart(6, '0') + 'k'.repeat(1024)] = i;
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as Record<string, unknown>;

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(capped._truncated).toBe(true);
    expect(typeof capped.content).toBe('string');
    expect((capped.content as string).startsWith('cccc')).toBe(true);
    expect(capped.type).toBe('final'); // 短值原样保留
  });

  it('P1：深到 stringify 爆栈时，深层大字符串仍计入并封顶', () => {
    // 这条 payload 让 JSON.stringify 与 depth<=2 的兜底 walker 同时失效：
    // 1 MiB 的字符串埋在 depth 3，浅层一个字符串都没有——计量若返回 0，
    // capTerminalEventData 会判定“没超预算”并原样放行整条终态。
    let deep: Record<string, unknown> = { end: true };
    for (let i = 0; i < 200000; i += 1) deep = { next: deep };
    const data = { wrap: { inner: { blob: 'x'.repeat(1024 * 1024), deep } } };
    expect(() => JSON.stringify(data)).toThrow();

    const capped = capTerminalEventData(data) as Record<string, unknown>;

    expect(capped).not.toBe(data); // 不许原样放行
    expect(() => JSON.stringify(capped)).not.toThrow();
    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
  });

  it('P1：代理对载荷略微超预算时仍走裁剪，不整条退化成摘要', () => {
    // 裁剪点对齐代理对后，若这一轮一个字符都没剪掉，轮次会空转到底并掉进
    // 摘要路径——emoji 内容会被整条摘成描述对象。这里锁住：树保留，只有
    // 超长字段被裁（且不留孤立半代理）。
    const data = { a: { b: '😀'.repeat(262144) } };
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as { a: { b: string }; _truncated?: boolean };

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(capped._truncated).toBeUndefined();
    expect(typeof capped.a.b).toBe('string');
    expect(capped.a.b.endsWith('…')).toBe(true);
    expect(hasLoneSurrogate(capped.a.b)).toBe(false);
  });

  it('P1：未超预算的嵌套载荷 identity 不变（含深树与数组）', () => {
    const data = { metadata: { list: ['a', 'b', { deep: 'c' }] }, content: 'ok' };
    expect(capTerminalEventData(data)).toBe(data);
  });

  it('P2：final + error 两条大终态先后入库——总量守住上限，最新终态保留', () => {
    const buf = createInFlightSnapshot();
    // 单条约 600 KiB：各自合法（capTerminalEventData 原样返回），相加超 1 MiB。
    const finalData = capTerminalEventData(bigPayload('f'.repeat(300 * 1024)));
    const errorData = capTerminalEventData(bigPayload('e'.repeat(300 * 1024)));
    expect(inFlightEventBytes({ type: 'final', data: finalData, timestamp: 9 } as Ev)).toBeLessThan(
      IN_FLIGHT_MAX_BYTES
    );

    pushInFlightEvent(buf, { type: 'final', data: finalData, timestamp: 9 } as Ev);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(1);

    pushInFlightEvent(buf, { type: 'error', data: errorData, timestamp: 10 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    // 最新终态保留完整数据；旧 final 只被掏空 payload，事件本身留下。
    const newest = buf.events[buf.events.length - 1];
    expect(newest.type).toBe('error');
    expect(newest.timestamp).toBe(10);
    expect((newest.data as { content: string }).content).toBe(errorData.content);
    // 回放契约：`turnDone`/`finalHandledSessions` 看的是 final 事件"在不在"
    // （Audit #1 的「思考中…」卡死守卫），所以 final 不能被整条删掉。
    expect(buf.events.some((e) => e.type === 'final')).toBe(true);
  });

  it('P2：final + aborted 同样组合——最新 aborted 保留，旧 final 只剩占位', () => {
    const buf = createInFlightSnapshot();
    const finalData = capTerminalEventData(bigPayload('f'.repeat(300 * 1024)));
    const abortedData = capTerminalEventData(bigPayload('a'.repeat(300 * 1024)));

    pushInFlightEvent(buf, { type: 'final', data: finalData, timestamp: 9 } as Ev);
    pushInFlightEvent(buf, { type: 'aborted', data: abortedData, timestamp: 11 } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    const newest = buf.events[buf.events.length - 1];
    expect(newest.type).toBe('aborted');
    expect(newest.timestamp).toBe(11);
    expect((newest.data as { content: string }).content).toBe(abortedData.content);
    // 旧 final 仅剩占位（type + timestamp），事件的"在场"保留。
    const oldest = buf.events[0];
    expect(oldest.type).toBe('final');
    expect((oldest.data as { _evicted?: boolean })._evicted).toBe(true);
    expect(inFlightEventBytes(oldest)).toBeLessThan(1024);
  });

  it('P2：error 被掏空时保留 message 头部，回放不显示「Unknown error」', () => {
    const buf = createInFlightSnapshot();
    const errorData = capTerminalEventData({ message: 'E'.repeat(300 * 1024) });
    pushInFlightEvent(buf, { type: 'error', data: errorData, timestamp: 8 } as Ev);
    pushInFlightEvent(buf, {
      type: 'final',
      data: capTerminalEventData(bigPayload('f'.repeat(300 * 1024))),
      timestamp: 9,
    } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    // 较旧的 error 先交出 payload，但保留 message 头部：cachedEventsToMessages
    // 会把 error 渲染成错误气泡，掏空 message 就变成「Unknown error」。
    const stripped = buf.events[0];
    expect(stripped.type).toBe('error');
    expect((stripped.data as { _evicted?: boolean })._evicted).toBe(true);
    const head = (stripped.data as { message?: unknown }).message;
    expect(typeof head).toBe('string');
    expect((head as string).startsWith('EEE')).toBe(true);
    expect((head as string).length).toBeLessThanOrEqual(200);
    // 最新终态（final）的数据完整保留。
    expect(
      (buf.events[buf.events.length - 1].data as { content: string }).content.startsWith('fff')
    ).toBe(true);
  });

  it('P2：掏空也救不回预算时不掏——不白扔内容', () => {
    // 最新事件是一条无法拆分的 progress（1.2 MiB 全在 delta 之外的字段里），
    // 它不能被驱逐。此时把 error 掏空也回不到上限内，所以必须原样留着
    // message——掏空前这条 error 有 30 万字的报错正文，掏空只换来一个
    // 仍然超限的结果。
    const buf = createInFlightSnapshot();
    const longMessage = 'BOOM: ' + 'x'.repeat(300 * 1024);
    pushInFlightEvent(buf, {
      type: 'error',
      data: capTerminalEventData({ message: longMessage }),
      timestamp: 8,
    } as Ev);
    pushInFlightEvent(buf, fatFieldProgress('p'.repeat(600 * 1024), 9));

    const errorEvent = buf.events[0];
    expect((errorEvent.data as { _evicted?: boolean })._evicted).toBeUndefined();
    expect((errorEvent.data as { message: string }).message).toBe(longMessage);
    // 已知边界：救不回来就如实超限，而不是毁数据换一个仍然超限的结果。
    expect(buf.bytes).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  });

  it('P2：终态自带 _evicted 字段不冒充"已掏空"', () => {
    // 后端字段名恰好也叫 _evicted 时，不能被当成我们的占位标记——否则这条
    // 终态永远不可回收，缓存超限且无计可施。
    const buf = createInFlightSnapshot();
    const payload = { _evicted: true, content: 'f'.repeat(300 * 1024) };
    pushInFlightEvent(buf, {
      type: 'final',
      data: capTerminalEventData(payload),
      timestamp: 8,
    } as Ev);
    pushInFlightEvent(buf, {
      type: 'final',
      data: capTerminalEventData(bigPayload('g'.repeat(300 * 1024))),
      timestamp: 9,
    } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    // 旧的那条被真正掏空（只剩占位 + error 之外的类型标记），新的完整保留。
    expect(Object.keys(buf.events[0].data as object).sort()).toEqual(['_evicted']);
    expect(
      (buf.events[buf.events.length - 1].data as { content: string }).content.startsWith('ggg')
    ).toBe(true);
  });

  it('P2：停在超限状态时一定已无可回收项（不白扔内容的不变量，逐次 push 检查）', () => {
    // 扫「n 条近满终态 × 尾部事件」共 18 种组合。不变量有两条：
    //   1) 若还能靠掏空回到上限内，就不允许停在超限状态——那是拿内容换了个
    //      仍然超限的结果；
    //   2) 记账恒等于事件字节之和。
    // 近满终态：约 1 MB/条（payload 压在终态预算之下，capTerminalEventData
    // 原样返回），两条就超限，能真正走到「掏空」与「掏空也救不回」两条路径。
    const nearMaxPayload = (ch: string) => capTerminalEventData({ blob: ch.repeat(500 * 1024) });
    const violations: string[] = [];
    let overBudgetStates = 0;

    // 每次 push 之后立刻检查：pushInFlightEvent 是同步回收的，超限状态就是
    // 回收后的结果，正是要断言的那一刻（只看最终态会漏掉中间过程）。
    const checkInvariant = (buf: ReturnType<typeof createInFlightSnapshot>, label: string) => {
      // 掏空每条尚未掏空的终态还能回收多少字节（与实现的占位形状一致：error
      // 保留 200 字 message 头部）。
      let reclaimable = 0;
      for (const e of buf.events) {
        if (e.type === 'progress' || isStrippedPayload(e.data)) continue;
        const message = (e.data as { message?: unknown } | null)?.message;
        const strippedData =
          typeof message === 'string'
            ? { _evicted: true, message: message.slice(0, 200) }
            : { _evicted: true };
        reclaimable +=
          inFlightEventBytes(e) - inFlightEventBytes({ type: e.type, data: strippedData } as Ev);
      }
      if (buf.bytes > IN_FLIGHT_MAX_BYTES) {
        overBudgetStates += 1;
        if (buf.bytes - reclaimable <= IN_FLIGHT_MAX_BYTES) {
          violations.push(`${label}: bytes=${buf.bytes} reclaimable=${reclaimable}`);
        }
      }
      const ledger = buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0);
      if (ledger !== buf.bytes) violations.push(`${label}: ledger ${ledger} != ${buf.bytes}`);
    };

    for (let n = 1; n <= 6; n += 1) {
      for (const tail of [
        'progress-delta-big',
        'progress-fat',
        'progress-small',
        'none',
      ] as const) {
        const buf = createInFlightSnapshot();
        const types = ['final', 'error', 'aborted'] as const;
        for (let i = 0; i < n; i += 1) {
          pushInFlightEvent(buf, {
            type: types[i % 3],
            data: nearMaxPayload(String.fromCharCode(102 + i)),
            timestamp: i,
          } as Ev);
          checkInvariant(buf, `n=${n} ${tail} push#${i}`);
        }
        if (tail === 'progress-delta-big') {
          // 可拆分的超大 delta：拆分 + 逐条回收必须把它压回总预算内（单事件
          // 上限落地后，「最新事件是超大 progress」不再能击穿快照上限）。
          pushInFlightEvent(buf, progress('p'.repeat(600 * 1024), 'stdout', 'c1', 99));
          checkInvariant(buf, `n=${n} ${tail} tail`);
          expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
          for (const e of buf.events) {
            expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
          }
        }
        if (tail === 'progress-fat') {
          // 拆不动的 progress（字节在 delta 之外）：这里才是「停在超限状态」
          // 的真实入口，不变量 1 要在这里被扫到。
          pushInFlightEvent(buf, fatFieldProgress('p'.repeat(600 * 1024), 99));
          checkInvariant(buf, `n=${n} ${tail} tail`);
        }
        if (tail === 'progress-small') {
          pushInFlightEvent(buf, {
            type: 'progress',
            data: { stream: 'stdout', delta: 'p'.repeat(1024), tool_call_id: 'c1' },
            timestamp: 99,
          } as Ev);
          checkInvariant(buf, `n=${n} ${tail} tail`);
        }
      }
    }
    // 扫到的组合必须真的走到过超限状态，否则这条不变量就是空转。
    expect(overBudgetStates).toBeGreaterThan(0);
    expect(violations).toEqual([]);
  });

  it('P2：三条满额终态时最新终态也让出 payload（最后手段分支）', () => {
    // 每条都贴着终态预算（payloadBytes ≈ TERMINAL_PAYLOAD_MAX_BYTES）。掏空两条
    // 旧的之后，剩下的"最新终态 + 两个占位"仍然超限，此时只能让最新终态也
    // 交出 payload——没有这个最后手段分支，这里就会停在超限状态。
    const atMax = (ch: string) =>
      capTerminalEventData({ blob: ch.repeat(Math.floor((TERMINAL_PAYLOAD_MAX_BYTES - 32) / 2)) });
    expect(
      inFlightEventBytes({ type: 'final', data: atMax('v'), timestamp: 0 } as Ev)
    ).toBeGreaterThan(IN_FLIGHT_MAX_BYTES / 3);

    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: atMax('f'), timestamp: 1 } as Ev);
    pushInFlightEvent(buf, { type: 'error', data: atMax('e'), timestamp: 2 } as Ev);
    pushInFlightEvent(buf, { type: 'aborted', data: atMax('a'), timestamp: 3 } as Ev);

    // 三条事件都还在（回放的终态判定不受影响），记账守恒，且确实回到上限内。
    expect(buf.events.map((e) => e.type)).toEqual(['final', 'error', 'aborted']);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
  });

  it('P2：环状载荷的字节计费走兜底 walker 且能终止（seen 路径）', () => {
    const cyc: Record<string, unknown> = { blob: 'z'.repeat(1024 * 1024) };
    cyc.self = cyc;

    // JSON.stringify 会抛，payloadBytes 必须落到全深度 walker：环要能被
    // seen 挡住，且 1 MiB 的深层字符串照样计入。
    expect(inFlightEventBytes({ type: 'final', data: cyc, timestamp: 9 } as Ev)).toBeGreaterThan(
      IN_FLIGHT_MAX_BYTES
    );

    const capped = capTerminalEventData(cyc);
    expect(capped).not.toBe(cyc);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: capped, timestamp: 9 } as Ev);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
  });

  it('P2：最新事件是贴着上限的 progress 时，终态让出 payload 把预算拉回来', () => {
    const atMax = (ch: string) =>
      capTerminalEventData({ blob: ch.repeat(Math.floor((TERMINAL_PAYLOAD_MAX_BYTES - 32) / 2)) });
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: atMax('f'), timestamp: 9 } as Ev);
    pushInFlightEvent(buf, {
      type: 'progress',
      data: { stream: 'stdout', delta: 'p'.repeat(300 * 1024), tool_call_id: 'c1' },
      timestamp: 10,
    } as Ev);

    // 终态贴着预算、progress 又是最新事件（不能被驱逐，watchdog 读它的时间戳
    // 判活）：只能让终态交出 payload——预算回到上限内，终态"在场"仍在（回放
    // 照旧判定 turnDone），时间戳不变。delta 拆分后 progress 侧再也不会越界，
    // 这条路径的入口只剩"终态自己贴着预算"。
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    expect(buf.events[0].type).toBe('final');
    expect((buf.events[0].data as { _evicted?: boolean })._evicted).toBe(true);
    expect(buf.events[0].timestamp).toBe(9);
    expect(buf.events[buf.events.length - 1].timestamp).toBe(10);
    for (const e of buf.events.slice(1)) {
      expect(e.type).toBe('progress');
      expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    }
  });

  it('P2：单条超大 delta 不再独占缓存（旧边界已被单事件上限消除）', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: { stream: 'stdout', delta: 'p'.repeat(600 * 1024), tool_call_id: 'c1' },
      timestamp: 1,
    });

    // 600 KiB delta（1.2 MiB 字节）以前是"唯一事件、无法回收、如实超限"的形状；
    // 拆分后它变成一列 ≤64 KiB 的 chunk，逐条回收即可守住快照上限（被丢掉的
    // 只是最旧的 chunk，回放拿到的仍是最近的一段，即原 delta 的后缀）。
    expect(buf.events.length).toBeGreaterThan(1);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events[buf.events.length - 1].timestamp).toBe(1);
    const kept = concatDeltas(buf);
    expect(kept.length).toBeGreaterThan(0);
    expect('p'.repeat(600 * 1024).endsWith(kept)).toBe(true);
  });

  it('P2：拆不动的超大 progress 独占缓存时仍无法回收（已知边界，如实锁住）', () => {
    // 字节藏在 delta 之外：拆分切不到它，又是唯一事件（最新事件永不驱逐），
    // 没有任何可回收对象。上限在此形状下照顾不到——与其假装守住，不如把
    // 行为钉死，形状变化时立刻可见。
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, fatFieldProgress('p'.repeat(600 * 1024), 1));

    expect(buf.bytes).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(1);
    expect(buf.events[0].timestamp).toBe(1);
  });

  it('P2：有 progress 可弃时不丢终态——先弃 progress，终态留在快照里', () => {
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: bigPayload('p'.repeat(300 * 1024)),
      timestamp: 8,
    } as Ev);
    pushInFlightEvent(buf, {
      type: 'final',
      data: capTerminalEventData(bigPayload('f'.repeat(300 * 1024))),
      timestamp: 9,
    } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.map((e) => e.type)).toEqual(['final']);
    expect(buf.events[0].timestamp).toBe(9);
  });
});

describe('#1034 复审 P1：active 终态同一套 payload cap（tool_calls 保持数组）', () => {
  /** active 路径（onFinal / onError）直接落到 renderer state 的字段。 */
  function activeFinal(content: string, toolCalls?: unknown[]): Record<string, unknown> {
    return { content, tool_calls: toolCalls, turn_id: 't1', session_key: 's1' };
  }

  function toolCallsOf(capped: unknown): unknown[] {
    const calls = (capped as { tool_calls?: unknown }).tool_calls;
    expect(Array.isArray(calls)).toBe(true);
    return calls as unknown[];
  }

  it('active final + 2 MiB content：封顶后有界，答案头部保留、控制字段原样', () => {
    const full = 'x'.repeat(2 * 1024 * 1024);
    const data = { content: full, turn_id: 't1', session_key: 's1' };
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as {
      content: string;
      turn_id: string;
      session_key: string;
    };

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(capped.content.length).toBeLessThan(full.length);
    expect(capped.content.startsWith('xxx')).toBe(true);
    expect(capped.turn_id).toBe('t1');
    expect(capped.session_key).toBe('s1');
  });

  it('active final + 超大 tool_calls：数组类型不变，元素保留 id/type/function.name', () => {
    const hugeArguments = 'a'.repeat(64 * 1024);
    const calls = Array.from({ length: 250 }, (_, i) => ({
      id: `call_${i}`,
      type: 'function',
      function: { name: `tool_${i}`, arguments: hugeArguments },
    }));
    const data = activeFinal('ok', calls);
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data);
    const stored = toolCallsOf(capped);

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(stored.length).toBeGreaterThan(0);
    expect(stored.length).toBeLessThan(250);
    expect((capped as { content: string }).content).toBe('ok'); // 答案不受影响
    for (const tc of stored) {
      const call = tc as { id?: string; type?: string; function?: { name?: string } };
      expect(call.type).toBe('function');
      expect(typeof call.id).toBe('string');
      expect(typeof call.function?.name).toBe('string');
    }
    expect((stored[0] as { id: string }).id).toBe('call_0');
  });

  it('active error + 2 MiB message：仍是字符串，回放不会变成 Unknown error', () => {
    const full = 'm'.repeat(2 * 1024 * 1024);
    const data = { message: full, code: 'E_BOOM', turn_id: 't1' };
    expect(jsonBytes(data)).toBeGreaterThan(TERMINAL_PAYLOAD_MAX_BYTES);

    const capped = capTerminalEventData(data) as { message: string; code: string };

    expect(jsonBytes(capped)).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    expect(typeof capped.message).toBe('string');
    expect(capped.message.length).toBeGreaterThan(0);
    expect(capped.message.length).toBeLessThan(full.length);
    expect(capped.message.startsWith('mmm')).toBe(true);
    expect(capped.code).toBe('E_BOOM'); // 短字段原样
    // 回放端读到的是非空字符串（空串会渲染成「Unknown error」）。
    expect(capped.message.trim().length).toBeGreaterThan(0);
  });

  it('各种超限形状下 tool_calls 始终是数组（含退化到摘要的路径）', () => {
    const shapes: Array<[string, unknown]> = [
      [
        '单条 arguments 巨大',
        activeFinal('ok', [{ function: { name: 'a', arguments: 'x'.repeat(2 * 1024 * 1024) } }]),
      ],
      [
        '大量 call',
        activeFinal(
          'ok',
          Array.from({ length: 3000 }, (_, i) => ({
            id: `c${i}`,
            function: { name: `t${i}`, arguments: 'y'.repeat(2048) },
          }))
        ),
      ],
      [
        '嵌套大字符串',
        activeFinal('ok', [
          {
            id: 'c0',
            function: { name: 'a', arguments: '{}' },
            input: { blob: 'z'.repeat(2 * 1024 * 1024) },
          },
        ]),
      ],
      ['非对象元素', activeFinal('ok', ['not-an-object', 'x'.repeat(1024 * 1024)])],
      [
        '字节藏在 key 里（退化摘要）',
        { tool_calls: [{ function: { name: 'a', arguments: '{}' } }], ...bigKeyPayload() },
      ],
    ];

    for (const [label, data] of shapes) {
      const capped = capTerminalEventData(data as object) as { tool_calls?: unknown };
      expect(Array.isArray(capped.tool_calls), `${label}: tool_calls 必须是数组`).toBe(true);
      expect(jsonBytes(capped), `${label}: 超预算`).toBeLessThanOrEqual(TERMINAL_PAYLOAD_MAX_BYTES);
    }
  });

  it('空 tool_calls 数组原样透传（isAssistantWithToolCalls 的判别不受影响）', () => {
    const data = { content: 'ok', tool_calls: [] };
    expect(capTerminalEventData(data)).toBe(data);
  });
});
