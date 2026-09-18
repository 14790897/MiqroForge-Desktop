/**
 * #1034 在途事件缓存（moduleInFlightCache）计数 + 累计字节双上限（RED→GREEN）。
 *
 * 该缓存只在用户切走后接管流事件（切回时回放）。原来 `buf.events.push(...)`
 * 无上界：一个长时间思考的会话在后台可以堆下十万级事件对象，且 20 个会话
 * 各有一份。这里锁死：连续同流 delta 先折叠（**无损**，回放端按顺序拼接
 * delta，见 ChatConsole 的 exec 输出回放），折叠后仍超限则按序回收——先驱逐
 * 最旧的 progress，再掏空较旧终态的 payload（保留 type/timestamp，回放的
 * 终态判定与 watchdog 不受影响），掏空后仍超限则驱逐较旧的**已掏空占位**
 * （保留"最新 terminal + 最后一条 final"，见 #1118 那一组）；最新终态自己的
 * payload 是最后手段，排在占位驱逐之后（它是这一回合的收尾行，而持久化历史
 * 可能还没追上这一回合）。最新事件永不驱逐。
 *
 * （#1118）终态占位不是零成本：每条仍留 type/timestamp（约 160 字节；error
 * 还留 200 字 message 头部，约 590 字节），几千条已结算终态自己就能压过 1 MiB，
 * 而"终态永不驱逐"让「只剩终态时条数上限 2000」形同虚设。现在只剩终态且仍
 * 超限时，较旧的占位整条驱逐——回放要的只有两条：最新 terminal（这一回合的
 * 收尾行，watchdog 判活读它的时间戳）与最后一条 final（回复正文从它来；
 * `turnDone` / `finalHandledSessions` 判的是
 * `events.some(e => e.type === 'final')`，不是「任意终态」）。
 *
 * （四轮复审）单条 progress 另有一条 64 KiB 硬上限，在入库前强制：delta 可以
 * 切片再拼回（无损），delta 之外的字节由 `sanitizeProgressEventData` 递归裁剪
 * 整段 payload（有损，只留头部），所以"最新事件是超大 progress"不再能击穿
 * 快照的 1 MiB 上限。终态不受这条约束，由 capTerminalEventData 单独封顶。
 */
import { describe, expect, it } from 'vitest';
import {
  IN_FLIGHT_EVENT_OVERHEAD_BYTES,
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
  sanitizeProgressEventData,
} from '../src/renderer/features/chat/ChatConsole';

/** 单条 progress 载荷的字节预算：单事件上限减去该事件自身的记账开销。与
 *  ChatConsole 里 `PROGRESS_PAYLOAD_MAX_BYTES` 同一算式（未导出）——用它断言
 *  载荷级口径，收紧了实现（严于这个数）不会变红，放松了会。 */
const PROGRESS_PAYLOAD_MAX_BYTES = IN_FLIGHT_MAX_EVENT_BYTES - IN_FLIGHT_EVENT_OVERHEAD_BYTES;

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

/** progress：大字节藏在 `delta` **之外**的字段里，按字节拆分救不了（拆分只能切
 *  `delta`）。第四轮起这条路径由 `sanitizeProgressEventData` 接管——对整个 payload
 *  递归有界化（有损：被裁字段只留头部 + 省略号），所以它不再能造出超限事件，而是
 *  「拆不动就必须裁」这条分支的入口。 */
function fatFieldProgress(payload: string, timestamp = 1): Ev {
  return {
    type: 'progress',
    data: { stream: 'stdout', delta: '', tool_call_id: 'c1', tool_output: payload },
    timestamp,
  } as Ev;
}

/** progress：大字节藏在**嵌套**字段里（`data.meta.details.huge`，深度 3），
 *  递归裁剪必须一路走到叶子。 */
function nestedFatProgress(payload: string, timestamp = 1): Ev {
  return {
    type: 'progress',
    data: {
      stream: 'stdout',
      delta: '',
      tool_call_id: 'c1',
      meta: { details: { huge: payload } },
    },
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

  // #1118 第八轮 P3：上面那条只测了一个到达顺序。合并的判定是「同 stream + 同
  // tool_call_id」两个键一起比的，两个键的独立性都得与到达顺序无关——回放端按
  // (stream, tool_call_id) 归行，任何一种顺序下错了都会把两条工具输出串成一条。
  describe('合并的身份键（两种到达顺序对称）', () => {
    const orders = [
      { label: 'c1 → c2', seq: ['c1', 'c2'] },
      { label: 'c2 → c1', seq: ['c2', 'c1'] },
    ] as const;

    for (const { label, seq } of orders) {
      it(`同 stream、不同 tool_call_id（${label}）→ 两条独立事件，顺序保持`, () => {
        const buf = createInFlightSnapshot();
        pushInFlightEvent(buf, progress('a', 'stdout', seq[0], 1));
        pushInFlightEvent(buf, progress('b', 'stdout', seq[1], 2));

        expect(buf.events.length).toBe(2);
        expect(buf.events.map((e) => (e.data as { tool_call_id: string }).tool_call_id)).toEqual([
          seq[0],
          seq[1],
        ]);
        expect(buf.events.map((e) => (e.data as { delta: string }).delta)).toEqual(['a', 'b']);
        // 归行结果：两条各自独立的文本，谁都没被并进对方
        expect(concatDeltas(buf, 'stdout', seq[0])).toBe('a');
        expect(concatDeltas(buf, 'stdout', seq[1])).toBe('b');
      });

      it(`同 stream、同 tool_call_id（${label} 之后）→ 合并成一条，时间戳取最新`, () => {
        const buf = createInFlightSnapshot();
        const id = seq[0];
        pushInFlightEvent(buf, progress('a', 'stdout', id, 1));
        pushInFlightEvent(buf, progress('b', 'stdout', id, 2));

        expect(buf.events.length).toBe(1);
        expect((buf.events[0].data as { delta: string }).delta).toBe('ab');
        expect(concatDeltas(buf, 'stdout', id)).toBe('ab');
        expect(buf.events[0].timestamp).toBe(2); // watchdog 读最新时间戳
      });
    }

    it('一边带 tool_call_id、一边没有 → 不合并（`?? null` 归一化后身份不同）', () => {
      // 缺 id 的 delta 属于「没有具名 tool call」那一行，与具名行不能混。
      const buf = createInFlightSnapshot();
      pushInFlightEvent(buf, {
        type: 'progress',
        data: { stream: 'stdout', delta: 'a' },
        timestamp: 1,
      } as Ev);
      pushInFlightEvent(buf, progress('b', 'stdout', 'c1', 2));

      expect(buf.events.length).toBe(2);
      expect((buf.events[0].data as { delta: string }).delta).toBe('a');
      expect((buf.events[1].data as { delta: string }).delta).toBe('b');
    });
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

  it('P2/#1118：掏空救不回预算时改驱逐较旧占位——快照回到两个上限内（旧「如实超限」已闭合）', () => {
    // 这个形状原来正是 reclaimable 守卫的判定条件：终态永不驱逐、掏空后仍留
    // 占位（error 还带 200 字 message 头部），几千条已结算终态自己就能压过快照
    // 预算 ⇒ 掏空也回不到上限内 ⇒ 守卫宁可如实超限。占位可驱逐之后这条路没了：
    // 掏空旧终态之后接着丢占位，两个上限都必须回到成立。
    const buf = createInFlightSnapshot();
    const message = 'BOOM: ' + 'x'.repeat(300 * 1024);
    const errorEvent = {
      type: 'error',
      data: capTerminalEventData({ message }),
      timestamp: 8,
    } as Ev;
    buf.events.push(errorEvent);
    buf.bytes += inFlightEventBytes(errorEvent);
    // 7000 条只剩占位的终态（约 1.1 MiB）——不可回收的那部分自己就超预算。
    for (let i = 0; i < 7000; i += 1) {
      const placeholder = { type: 'final', data: { _evicted: true }, timestamp: 100 + i } as Ev;
      buf.events.push(placeholder);
      buf.bytes += inFlightEventBytes(placeholder);
    }
    expect(buf.bytes).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);

    // 任何一次 push 都会跑回收：没有可弃的 progress，于是先掏空较旧终态的
    // payload（第 2 步），再驱逐较旧的占位（第 3 步）。
    pushInFlightEvent(buf, {
      type: 'progress',
      data: { stream: 'stderr', delta: 'x', tool_call_id: 'c2' },
      timestamp: 10,
    } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENTS);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    // 驱逐按最旧优先：最旧的那些（含"先被掏空、随即整条让位"的那条 error）走人，
    // 活下来的是尾部占位——最新 terminal 仍在场，回放判定不受影响。
    const terminals = buf.events.filter((e) => e.type !== 'progress');
    expect(terminals[terminals.length - 1].type).toBe('final');
    expect(terminals[terminals.length - 1].timestamp).toBe(100 + 6999);
    expect(terminals.some((e) => e.timestamp === 100 + 6998)).toBe(true); // 次新的还在
    expect(terminals.some((e) => e.timestamp === 100)).toBe(false); // 最旧的已被驱逐
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

  it('P2：每次 push 后两个不变量都成立（单条 ≤64 KiB、总量 ≤1 MiB、记账守恒）', () => {
    // 扫「n 条近满终态 × 尾部事件」共 24 种组合，逐个 push 之后立刻检查：
    //   1) 进快照的每条事件都在单事件上限内；
    //   2) 总量不超过快照上限——包括「最新事件是超大/拆不动的 progress」这种
    //      第四轮之前会如实超限的形状；
    //   3) 记账恒等于事件字节之和。
    // 第四轮起 1) 由 pushInFlightEvent 在入库前保证（拆 delta 或递归裁剪整段
    // payload），2) 因此才是可证的：回收总能拿到够用的可回收项。
    // 近满终态：约 1 MB/条（payload 压在终态预算之下，capTerminalEventData
    // 原样返回），两条就超限，能真正走到「掏空」路径。
    const nearMaxPayload = (ch: string) => capTerminalEventData({ blob: ch.repeat(500 * 1024) });
    const violations: string[] = [];
    let sawStrippedTerminal = false;
    let sawEvictedProgress = false;

    // 每次 push 之后立刻检查：pushInFlightEvent 是同步回收的，"超限"状态只会
    // 是回收后的结果，正是要断言的那一刻（只看最终态会漏掉中间过程）。
    const checkInvariant = (buf: ReturnType<typeof createInFlightSnapshot>, label: string) => {
      const ledger = buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0);
      if (ledger !== buf.bytes) violations.push(`${label}: ledger ${ledger} != ${buf.bytes}`);
      if (buf.bytes > IN_FLIGHT_MAX_BYTES) {
        violations.push(`${label}: 总量 ${buf.bytes} 超限`);
      }
      for (const e of buf.events) {
        // 单事件 64 KiB 上限只约束 progress（delta 可以再拼回来）；终态由自己的
        // payload cap 管——`final` 的正文就是答案本身，不能像流式 delta 那样
        // 切片（见 ChatConsole 里两条 cap 的注释）。
        const cap = e.type === 'progress' ? IN_FLIGHT_MAX_EVENT_BYTES : IN_FLIGHT_MAX_BYTES;
        if (inFlightEventBytes(e) > cap) {
          violations.push(`${label}: 单条 ${inFlightEventBytes(e)} 超限（${e.type}）`);
        }
        if (isStrippedPayload(e.data)) sawStrippedTerminal = true;
      }
    };

    /** push 之后再检查两个不变量。 */
    const pushAndCheck = (
      buf: ReturnType<typeof createInFlightSnapshot>,
      event: Ev,
      label: string
    ) => {
      pushInFlightEvent(buf, event);
      checkInvariant(buf, label);
    };

    /** 可识别的旧 progress：用来证明扫描里真的走到过"驱逐最旧 progress"。
     *  条数变化看不出来——超大 delta 会在一次 push 里拆成一列 chunk，中间丢
     *  几条、净增仍是正的——所以这里放一条只有回收才会让它消失的标记事件。 */
    const isMarker = (e: Ev): boolean => (e.data as { stream?: string }).stream === 'marker-drop';

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
          pushAndCheck(
            buf,
            {
              type: types[i % 3],
              data: nearMaxPayload(String.fromCharCode(102 + i)),
              timestamp: i,
            } as Ev,
            `n=${n} ${tail} push#${i}`
          );
        }
        if (tail === 'progress-delta-big') {
          // 可拆分的超大 delta：拆分 + 逐条回收必须把它压回总预算内。
          pushAndCheck(buf, progress('m', 'marker-drop', 'c-marker', 98), `n=${n} ${tail} 标记`);
          expect(buf.events.some(isMarker)).toBe(true); // 先确认它在场
          pushAndCheck(
            buf,
            progress('p'.repeat(600 * 1024), 'stdout', 'c1', 99),
            `n=${n} ${tail} tail`
          );
          // 标记是最旧的 progress，回收按"最旧的 progress 先丢"把它挤出去。
          if (!buf.events.some(isMarker)) sawEvictedProgress = true;
        }
        if (tail === 'progress-fat') {
          // 拆不动的 progress（字节在 delta 之外）：第四轮之前它会以 1.2 MiB 的
          // 单条事件留在快照里，是"如实超限"的真实入口；现在入库前就被裁剪，
          // 两个不变量在这里同样成立。
          pushAndCheck(buf, fatFieldProgress('p'.repeat(600 * 1024), 99), `n=${n} ${tail} tail`);
        }
        if (tail === 'progress-small') {
          pushAndCheck(
            buf,
            {
              type: 'progress',
              data: { stream: 'stdout', delta: 'p'.repeat(1024), tool_call_id: 'c1' },
              timestamp: 99,
            } as Ev,
            `n=${n} ${tail} tail`
          );
        }
      }
    }
    // 扫描必须真的走到过回收（掏空终态 / 驱逐旧 progress），否则不变量是空转。
    expect(sawStrippedTerminal).toBe(true);
    expect(sawEvictedProgress).toBe(true);
    expect(violations).toEqual([]);
  });

  it('P2（第八轮）：掏空不降反增的短终态不被替换——正文不白丢，回收另找出路', () => {
    // 形状：一条正文极短的 error（`{message:'x'}` → 占位 `{_evicted:true,message:'x'}`
    // 反而**更大**），后面跟着两条各自贴着快照预算的大终态。两次大终态入库就顶破
    // 1 MiB，回收必须动手，而 step 2 的第一个候选正是那条短 error。
    // 修复前：先把它换成更大的占位（正文降级、预算反增），下一次循环再被 Step 3
    // 当"已掏空占位"优先驱逐——正文白丢两次，回收的字节还得另找一条大终态出。
    // 修复后：不划算就不替换，直接落 Step 3（这里没有可弃项）→ Step 4 掏空最新
    // 终态的 payload，短 error 原样活着。
    const shortError = {
      type: 'error',
      data: capTerminalEventData({ message: 'x' }),
      timestamp: 1,
    } as Ev;
    const strippedForm = {
      type: 'error',
      data: { _evicted: true, message: 'x' },
      timestamp: 1,
    } as Ev;
    // 前提：占位确实更大（否则这条用例考的不是"不降反增"）
    expect(inFlightEventBytes(strippedForm)).toBeGreaterThan(inFlightEventBytes(shortError));

    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, shortError);
    const f1 = {
      type: 'final',
      data: capTerminalEventData({ content: 'f'.repeat(300 * 1024) }),
      timestamp: 2,
    } as Ev;
    const f2 = {
      type: 'final',
      data: capTerminalEventData({ content: 'g'.repeat(300 * 1024) }),
      timestamp: 3,
    } as Ev;
    pushInFlightEvent(buf, f1);
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES); // 一条还不超
    const before = buf.bytes + inFlightEventBytes(f2);
    expect(before).toBeGreaterThan(IN_FLIGHT_MAX_BYTES); // 前提：第二条必须触发回收

    pushInFlightEvent(buf, f2);

    // 不变量：记账守恒、回到上限内，且回收这一拍不涨账（swap 不降反增就不会发生）
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.bytes).toBeLessThanOrEqual(before);
    // 主断言：短 error 的正文没有被换成占位（修复前这里是 `_evicted: true`）
    const err = buf.events.find((e) => e.type === 'error');
    expect(err).toBeDefined();
    expect((err!.data as { _evicted?: boolean })._evicted).toBeUndefined();
    expect((err!.data as { message?: string }).message).toBe('x');
    // 最新终态仍在场（watchdog 读它的时间戳），预算由它让出 payload 换回来
    const newest = buf.events[buf.events.length - 1];
    expect(newest.type).toBe('final');
    expect(newest.timestamp).toBe(3);
    expect((newest.data as { _evicted?: boolean })._evicted).toBe(true);
    expect(buf.events.some((e) => e.type === 'final')).toBe(true);
  });

  it('P2/#1118：三条满额终态时让位的是较旧的占位，最新终态保住 payload', () => {
    // 每条都贴着终态预算（payloadBytes ≈ TERMINAL_PAYLOAD_MAX_BYTES）。掏空两条
    // 旧的之后，剩下的"最新终态 + 两个占位"仍然超限——#1118 之后这一步不再拿
    // 最新终态的 payload 开刀（它是回放要渲染的回复，而持久化历史可能还没追上
    // 这一回合），改为驱逐较旧的那条占位：只有"连占位都没有"时最新终端才让出
    // payload（见下一条用例）。
    const atMax = (ch: string) =>
      capTerminalEventData({ blob: ch.repeat(Math.floor((TERMINAL_PAYLOAD_MAX_BYTES - 32) / 2)) });
    expect(
      inFlightEventBytes({ type: 'final', data: atMax('v'), timestamp: 0 } as Ev)
    ).toBeGreaterThan(IN_FLIGHT_MAX_BYTES / 3);

    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'final', data: atMax('f'), timestamp: 1 } as Ev);
    pushInFlightEvent(buf, { type: 'error', data: atMax('e'), timestamp: 2 } as Ev);
    pushInFlightEvent(buf, { type: 'aborted', data: atMax('a'), timestamp: 3 } as Ev);

    // 记账守恒、回到上限内；回放依仗的两条（最后一条 final + 最新 terminal）都在。
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.map((e) => e.type)).toEqual(['final', 'aborted']);
    // 最新终态完整（没被掏空），被驱逐的是中间那条已被掏空的 error 占位。
    expect(
      (buf.events[buf.events.length - 1].data as { blob: string }).blob.startsWith('aaa')
    ).toBe(true);
    expect((buf.events[0].data as { _evicted?: boolean })._evicted).toBe(true);
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

  it('P2：拆不动的超大 progress 独占缓存时被就地裁剪（旧边界已闭合）', () => {
    // 字节藏在 delta 之外：拆分切不到它，它又是唯一事件（最新事件永不驱逐，
    // watchdog 读它的时间戳）——第四轮之前这是单事件上限唯一照顾不到的形状，
    // 只能如实超限。现在整段 payload 在入库前被递归有界化，所以"独占缓存"
    // 不再等于"超限"：单条 ≤64 KiB、总量 ≤1 MiB、时间戳与在场性都不变。
    const buf = createInFlightSnapshot();
    const payload = 'p'.repeat(600 * 1024);
    pushInFlightEvent(buf, fatFieldProgress(payload, 1));

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(1);
    expect(buf.events[0].timestamp).toBe(1);
    expect(inFlightEventBytes(buf.events[0])).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
  });

  it('P2：有 progress 可弃时不丢终态——先弃 progress，终态留在快照里', () => {
    // 单条 progress 现在最多 64 KiB，撑不爆 1 MiB 的预算，所以这里要真的堆够
    // 字节：先推一条 delta 之外塞满大字段的 progress（入库时被裁到远小于上限，
    // 但仍然是真事件），再推一条贴着终态预算的 final——两条之和必然超限，回收
    // 只能落在 progress 上（终态是回放判定 turnDone 的依据）。
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, fatFieldProgress('p'.repeat(600 * 1024), 8));
    expect(inFlightEventBytes(buf.events[0])).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
    pushInFlightEvent(buf, {
      type: 'final',
      data: capTerminalEventData({
        blob: 'f'.repeat(Math.floor((TERMINAL_PAYLOAD_MAX_BYTES - 32) / 2)),
      }),
      timestamp: 9,
    } as Ev);

    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.map((e) => e.type)).toEqual(['final']);
    expect(buf.events[0].timestamp).toBe(9);
  });
});

describe('#1034 复审四轮 P1：progress 全 payload 硬上限（delta 之外的字节同样有界）', () => {
  /** 契约：push 之后，快照里每条 **progress** 事件都在单事件上限内、总量在快照
   *  上限内、记账守恒。只用于本组的纯 progress 缓冲区——终态由另一条（更宽的）
   *  TERMINAL_PAYLOAD_MAX_BYTES 管，不适用单事件上限。 */
  function expectBounded(buf: ReturnType<typeof createInFlightSnapshot>, label: string): void {
    const ledger = buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0);
    expect(ledger, `${label}: 记账`).toBe(buf.bytes);
    expect(buf.bytes, `${label}: 总量`).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    for (const e of buf.events) {
      expect(inFlightEventBytes(e), `${label}: 单条`).toBeLessThanOrEqual(
        IN_FLIGHT_MAX_EVENT_BYTES
      );
    }
  }

  /** 拆 delta 救不了的各种形状：字节在 delta 之外的顶层字段、嵌套字段、纯 key、
   *  无 delta 的 lifecycle 事件。 */
  function fatShapes(): Array<[string, Ev]> {
    return [
      ['tool_output（顶层非 delta 字段）', fatFieldProgress('p'.repeat(600 * 1024), 9)],
      ['data.meta.details.huge（嵌套字段）', nestedFatProgress('n'.repeat(600 * 1024), 9)],
      [
        'doc_progress 的超大 file（无 delta）',
        {
          type: 'progress',
          data: { type: 'doc_progress', file: 'f'.repeat(600 * 1024), stage: 'ready' },
          timestamp: 9,
        } as Ev,
      ],
      ['deep tool_calls.arguments（深度 ≥3）', deepToolCallProgress('a'.repeat(600 * 1024), 9)],
      [
        '字节全在 key 里（裁无可裁）',
        {
          type: 'progress',
          data: { stream: 'stdout', tool_call_id: 'c1', ...bigKeyPayload() },
          timestamp: 9,
        } as Ev,
      ],
    ];
  }

  it('P1：拆不动的超大 progress 入库后单条 ≤64 KiB、总量 ≤1 MiB（各形状逐个扫）', () => {
    for (const [label, event] of fatShapes()) {
      expect(inFlightEventBytes(event), `${label}: 用例本身要超限`).toBeGreaterThan(
        IN_FLIGHT_MAX_EVENT_BYTES
      );
      const buf = createInFlightSnapshot();
      pushInFlightEvent(buf, event);
      expectBounded(buf, label);
      // 有界化不改变"在场性"：仍然是一条 progress，时间戳原样（watchdog 靠它判活）。
      expect(buf.events.length, `${label}: 事件数`).toBe(1);
      expect(buf.events[0].type, `${label}: 类型`).toBe('progress');
      expect(buf.events[0].timestamp, `${label}: 时间戳`).toBe(9);
    }
  });

  it('P1：裁的是内容不是协议——stream/tool_call_id 原样，被裁字段留头部', () => {
    const payload = 'p'.repeat(600 * 1024);
    // 100 字的 session_key/tool_call_id：短于协议字段的裁剪宽度（256），但长于
    // 递归裁剪的最小可裁长度（64）——递归那一遍必须跳过它们，否则会被砍半。
    const sessionKey = 'k'.repeat(100);
    const callId = 'c'.repeat(100);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: {
        stream: 'stdout',
        delta: '',
        tool_call_id: callId,
        session_key: sessionKey,
        // 嵌套大字段不在第 1/2 步的按宽裁剪范围内，只有它能把第 3 步（递归
        // 裁剪）真的拉起来——否则前两步已经把载荷压回预算内，协议字段是否被
        // 递归跳过就无从验证。
        meta: { details: { huge: 'n'.repeat(600 * 1024) } },
        tool_output: payload,
      },
      timestamp: 9,
    } as Ev);

    const data = buf.events[0].data as {
      stream?: string;
      tool_call_id?: string;
      session_key?: string;
      delta?: string;
      tool_output?: string;
    };
    // 回放要用的路由字段（exec 输出按 stream + tool_call_id 归行，#212 按
    // session_key 过滤）一个字不动。
    expect(data.stream).toBe('stdout');
    expect(data.tool_call_id).toBe(callId);
    expect(data.session_key).toBe(sessionKey);
    expect(data.delta).toBe('');
    // 内容字段有损：保留头部（工具结果卡片的表头还在），尾部丢弃并打省略号。
    expect(typeof data.tool_output).toBe('string');
    expect((data.tool_output as string).startsWith('ppp')).toBe(true);
    expect((data.tool_output as string).length).toBeLessThan(payload.length);
    expect((data.tool_output as string).endsWith('…')).toBe(true);
  });

  it('P1：doc_progress 的 file/stage 原样（附件行靠它们落格）', () => {
    const file = 'f'.repeat(600 * 1024);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: { type: 'doc_progress', file, stage: 'ready' },
      timestamp: 9,
    } as Ev);

    const data = buf.events[0].data as { type?: string; file?: string; stage?: string };
    expect(data.type).toBe('doc_progress');
    expect(data.stage).toBe('ready');
    // file 是协议字段：只按协议宽度留头部，不会被递归裁剪吃掉整条路径。
    expect((data.file as string).startsWith('fff')).toBe(true);
    expect((data.file as string).endsWith('…')).toBe(true);
    expect((data.file as string).length).toBeLessThan(file.length);
  });

  it('P1：嵌套大字段被递归裁剪，中间层结构不变', () => {
    const huge = 'n'.repeat(600 * 1024);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, nestedFatProgress(huge, 9));

    const data = buf.events[0].data as {
      stream?: string;
      meta?: { details?: { huge?: string } };
    };
    expect(data.stream).toBe('stdout');
    expect(typeof data.meta?.details?.huge).toBe('string');
    expect((data.meta?.details?.huge as string).startsWith('nnn')).toBe(true);
    expect((data.meta?.details?.huge as string).length).toBeLessThan(huge.length);
  });

  it('P1：裁无可裁时退化为有界摘要，仍带 _truncated 标记与协议字段', () => {
    // 协议字段**排在几千个垃圾 key 之后**：摘要按字段出现顺序取前 64 个的话，
    // 它们会被挤掉（live 处理器按 stream + tool_call_id 归行、doc_progress 靠
    // type + file 落格），所以这里钉住"协议字段先写"。
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: {
        ...bigKeyPayload(),
        stream: 'stdout',
        tool_call_id: 'c1',
        type: 'doc_progress',
        file: 'report.pdf',
        session_key: 's1',
      },
      timestamp: 9,
    } as Ev);

    const stored = buf.events[0].data as Record<string, unknown>;
    expect(stored._truncated).toBe(true);
    // 摘要不是"什么都不剩"：协议字段先写，不会被几千个 key 挤出字段预算。
    expect(stored.stream).toBe('stdout');
    expect(stored.tool_call_id).toBe('c1');
    expect(stored.type).toBe('doc_progress');
    expect(stored.file).toBe('report.pdf');
    expect(stored.session_key).toBe('s1');
    // 字段数被 MAX_SUMMARY_FIELDS 卡住，字节有界。
    expect(Object.keys(stored).length).toBeLessThan(100);
    expect(jsonBytes(stored)).toBeLessThanOrEqual(PROGRESS_PAYLOAD_MAX_BYTES);
  });

  it('P1：delta 与 delta 之外同时超限时，回放拿到的是 delta 头部（有损，非拼接）', () => {
    // 拆分是无损的，但它救不了"delta 之外已经超限"的形状：`fits('')` 为假时
    // 拆分直接放弃，改由递归裁剪兜底——delta 也被当成 bulk 字段只留头部。
    // 替代行为：回放这段 exec 输出时看到的是原文本的前缀 + 省略号，不再是原
    // 文（原 delta 仍完整落在这条事件对应的会话持久化历史里）。
    const delta = 'd'.repeat(600 * 1024);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, {
      type: 'progress',
      data: {
        stream: 'stdout',
        delta,
        tool_call_id: 'c1',
        tool_output: 'o'.repeat(600 * 1024),
      },
      timestamp: 1,
    } as Ev);

    expectBounded(buf, 'delta + 非 delta 字段同时超限');
    const kept = concatDeltas(buf);
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.length).toBeLessThan(delta.length);
    expect(delta.startsWith(kept.replace(/…$/, ''))).toBe(true);
  });

  it('P1：未超预算的 progress 原样入库（同一引用，无损路径不复制）', () => {
    const event = progress('hello', 'stdout', 'c1', 1);
    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, event);
    expect(buf.events[0]).toBe(event);
    expect(inFlightEventBytes(event)).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENT_BYTES);
  });

  it('P1：sanitizeProgressEventData 只读入参——bridge 把同一对象交给 live 处理器', () => {
    const huge = 'p'.repeat(600 * 1024);
    const data = { stream: 'stdout', delta: '', tool_call_id: 'c1', tool_output: huge };
    const capped = sanitizeProgressEventData(data) as Record<string, unknown>;

    expect(capped).not.toBe(data); // 超预算 → 复制而不是就地改
    expect(data.tool_output).toBe(huge); // 原对象的字段没被动过
    expect(capped.stream).toBe('stdout');
    expect(jsonBytes(capped)).toBeLessThanOrEqual(PROGRESS_PAYLOAD_MAX_BYTES);
  });

  it('P1：递归裁剪（第 3 步）也是 copy-on-write——嵌套容器不会被就地改写', () => {
    // 第 3 步走 replacePath 逐层重建容器；写错方向就会就地改掉 live 处理器
    // 正在用的那个对象（同一份 payload 由 bridge 交给两边）。
    const data = {
      stream: 'stdout',
      delta: '',
      tool_call_id: 'c1',
      meta: { details: { huge: 'n'.repeat(600 * 1024), note: 'keep' } },
    };
    const nested = data.meta.details;

    const capped = sanitizeProgressEventData(data) as typeof data;

    expect(capped).not.toBe(data);
    expect(capped.meta).not.toBe(data.meta);
    expect(capped.meta.details).not.toBe(nested);
    expect(nested.huge.length).toBe(600 * 1024); // 原嵌套对象一字未动
    expect(nested.note).toBe('keep');
    expect((capped.meta.details.huge as string).length).toBeLessThan(600 * 1024);
    expect(jsonBytes(capped)).toBeLessThanOrEqual(PROGRESS_PAYLOAD_MAX_BYTES);
  });

  it('P1：sanitizeProgressEventData 对未超预算的载荷保持同一引用', () => {
    const small = { stream: 'stdout', delta: 'hi', tool_call_id: 'c1' };
    expect(sanitizeProgressEventData(small)).toBe(small);
  });

  it('P1：递归裁剪点不落在代理对中间（不留孤立半代理）', () => {
    // 载荷放在**嵌套**字段里：`tool_output` 会被第 1 步按 4096 字宽预裁，那样
    // 就到不了第 3 步的递归裁剪，对齐逻辑也就没被考到。代理对取奇数个（150001），
    // 让"取一半"的裁剪点正落在某个低位代理上——不对齐就会留下孤立半代理。
    const huge = '😀'.repeat(150001);
    const data = {
      stream: 'stdout',
      delta: '',
      tool_call_id: 'c1',
      meta: { details: { huge } },
    };
    const capped = sanitizeProgressEventData(data) as {
      meta: { details: { huge: string } };
    };

    expect(hasLoneSurrogate(capped.meta.details.huge)).toBe(false);
    expect(jsonBytes(capped)).toBeLessThanOrEqual(PROGRESS_PAYLOAD_MAX_BYTES);
    // 头保留：第一个码点还在（裁剪点没被前移到别处）。
    expect(capped.meta.details.huge.startsWith('😀')).toBe(true);
    expect(capped.meta.details.huge.length).toBeLessThan(huge.length);
    // 这条用例是有牙的：裁剪点（本载荷取一半 = 下标 150001）正落在某个对的
    // 低位代理上，不对齐就会留下孤立半代理——上面那条断言因此真的在考对齐。
    expect(hasLoneSurrogate(huge.slice(0, 150001))).toBe(true);
  });

  it('P1：JSON.stringify 走不通的载荷里，藏在 key 里的字节同样计入并封顶', () => {
    // 环让 stringify 抛错，落到 walkPayloadBytes；字节全在 key 名里（value 是
    // 数字，没有可裁的字符串）。只数 value 的话这种载荷会被记成几百字节、原样
    // 入库——账面上"有界"，实际留下了 12 MB。
    const data: Record<string, unknown> = { stream: 'stdout', delta: '', tool_call_id: 'c1' };
    for (let i = 0; i < 20000; i += 1) data[`${i}`.padStart(6, '0') + 'k'.repeat(300)] = i;
    data.self = data;
    expect(() => JSON.stringify(data)).toThrow();

    const buf = createInFlightSnapshot();
    pushInFlightEvent(buf, { type: 'progress', data, timestamp: 9 } as Ev);

    expectBounded(buf, '环 + key 里的字节');
    const stored = buf.events[0].data as Record<string, unknown>;
    // 裁无可裁（key 不是字符串值）→ 退化为有界摘要，只留 64 个字段。
    expect(stored._truncated).toBe(true);
    expect(stored.stream).toBe('stdout');
    expect(Object.keys(stored).length).toBeLessThan(100);
  });

  it('P1：合并路径同样维持单事件上限——两条合法 delta 相加超限时各自成条', () => {
    const buf = createInFlightSnapshot();
    // 各 20 KiB 字符（40 KiB 字节）：单条合法（<64 KiB）。
    const half = 'h'.repeat(20 * 1024);
    pushInFlightEvent(buf, progress(half, 'stdout', 'c1', 1));
    pushInFlightEvent(buf, progress(half, 'stdout', 'c1', 2));

    // 合并后的 40 KiB 字符 = 80 KiB 字节 > 单事件上限 → 拒绝合并，两条各自有界。
    expect(buf.events.length).toBe(2);
    expectBounded(buf, '合并被拒');
    expect(concatDeltas(buf)).toBe(half + half);
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

describe('#1118：结算终态占位也可驱逐（快照 1 MiB / 条数 2000 对任意终态条数都成立）', () => {
  /** 直接把一条已掏空的占位推进快照（绕过 pushInFlightEvent）。模拟"几千条
   *  已结算终态"的存量：它们只在直接操作快照时出现——一次 push 之后回收就
   *  会把存量拉回上限内。 */
  function seedPlaceholder(
    buf: ReturnType<typeof createInFlightSnapshot>,
    type: 'final' | 'error' | 'aborted',
    timestamp: number
  ): Ev {
    const placeholder = { type, data: { _evicted: true }, timestamp } as Ev;
    buf.events.push(placeholder);
    buf.bytes += inFlightEventBytes(placeholder);
    return placeholder;
  }

  /** 两个不变量 + 记账守恒，任意一次操作之后都必须成立。 */
  function expectInvariants(buf: ReturnType<typeof createInFlightSnapshot>): void {
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBeLessThanOrEqual(IN_FLIGHT_MAX_EVENTS);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  }

  it('数千条 stripped 终态占位：快照回到 1 MiB / 2000 条内，最新 terminal 仍在', () => {
    // 只由占位构成的存量：每条 ~160 字节，7000 条自己就压过 1 MiB。旧策略下
    // 「终态永不驱逐」⇒ 掏空已经是它们的极限，而条数上限对只剩终态的快照形同
    // 虚设 ⇒ 只能如实超限。现在占位本身可弃。
    const buf = createInFlightSnapshot();
    for (let i = 0; i < 7000; i += 1) {
      seedPlaceholder(buf, 'final', 100 + i);
    }
    expect(buf.bytes).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBeGreaterThan(IN_FLIGHT_MAX_EVENTS);

    // 一次 push 就触发回收（没有可弃的 progress，只剩"丢占位"一条路）。
    pushInFlightEvent(buf, progress('p', 'stdout', 'c1', 9000));

    expectInvariants(buf);
    // 保留最新 terminal：它是 watchdog 的判活时间戳来源，也是回放 settle 的那条。
    const terminals = buf.events.filter((e) => e.type !== 'progress');
    expect(terminals[terminals.length - 1].timestamp).toBe(100 + 6999);
    // 最旧优先：尾部占位整段留下，头部整段被驱逐。
    expect(terminals.some((e) => e.timestamp === 100 + 6998)).toBe(true);
    expect(terminals.some((e) => e.timestamp === 100)).toBe(false);
  });

  it('混合场景（旧 terminal + 新 progress + 新 final）：不变量成立且最新终态在场', () => {
    const buf = createInFlightSnapshot();
    // 一条未掏空的旧终态（约 300 KiB）——第 2 步"掏空旧终态"的对象。
    const oldFinal = {
      type: 'final',
      data: capTerminalEventData({ content: 'o'.repeat(150 * 1024) }),
      timestamp: 1,
    } as Ev;
    pushInFlightEvent(buf, oldFinal);
    // 存量占位（5000 条 ≈ 800 KiB）：与旧终态一起同时压过字节与条数上限。
    for (let i = 0; i < 5000; i += 1) {
      seedPlaceholder(buf, 'aborted', 2 + i);
    }
    expect(buf.bytes).toBeGreaterThan(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBeGreaterThan(IN_FLIGHT_MAX_EVENTS);

    // 新回合：先 progress，后 final（两条都很小——回放要渲染的就是这条 final）。
    pushInFlightEvent(buf, progress('p'.repeat(1000), 'stdout', 'c1', 9000));
    const newFinal = { type: 'final', data: { content: 'the answer' }, timestamp: 9001 } as Ev;
    pushInFlightEvent(buf, newFinal);

    expectInvariants(buf);
    // 较旧的未掏空终态按第 2 步先交出 payload（旧策略不变），事件仍在原位。
    expect(buf.events[0].type).toBe('final');
    expect((buf.events[0].data as { _evicted?: boolean })._evicted).toBe(true);
    // 最新终态就是刚推入的 final：在场、完整（最后一条，watchdog 的时间戳也在它身上）。
    expect(buf.events[buf.events.length - 1]).toBe(newFinal);
    expect((newFinal.data as { content: string }).content).toBe('the answer');
    // 回放的 turnDone / finalHandledSessions 判的是 `some(type === 'final')`。
    expect(buf.events.some((e) => e.type === 'final')).toBe(true);
    // 存量的掏空占位按最旧优先整条驱逐（尾巴留下）。
    const terminals = buf.events.filter((e) => e.type !== 'progress');
    expect(terminals[terminals.length - 2].timestamp).toBe(2 + 4999);
    expect(terminals.some((e) => e.timestamp === 2)).toBe(false);
  });

  it('占位足够时不动最新终态的 payload：回复不为占位让路（驱逐优先于最后手段掏空）', () => {
    // 都是 ~160 字节、正文早已不在的占位，只有最后推入的那条 final 有正文——它是
    // 回放要渲染的回复，而持久化历史可能还没追上这一回合。所以先丢占位：把
    // "掏空最新终态"排在驱逐之前（#1118 之前的顺序）会让这里只剩一个空壳，
    // 正文全部丢失，而占位一条没少。
    const buf = createInFlightSnapshot();
    for (let i = 0; i < 3500; i += 1) {
      seedPlaceholder(buf, 'final', 2 + i);
    }
    const answer = 'A'.repeat(260 * 1024);
    const newest = {
      type: 'final',
      data: capTerminalEventData({ content: answer }),
      timestamp: 9000,
    } as Ev;
    pushInFlightEvent(buf, newest);

    expectInvariants(buf);
    // 最新终态还是原来那条事件（没被换成占位），正文一字不少。
    const last = buf.events[buf.events.length - 1];
    expect(last).toBe(newest);
    expect((last.data as { content: string }).content).toBe(answer);
    // 让位的是占位：条数被压回上限内，说明丢的确实是那些 ~160 字节的存量。
    expect(buf.events.filter((e) => e.type !== 'progress').length).toBe(IN_FLIGHT_MAX_EVENTS);
  });

  it('最后一条 final 不因驱逐消失（turnDone 的 `some(final)` 不回归）', () => {
    // 唯一的 final 是最旧的事件，其余全是 error/aborted 占位，最新 terminal 是
    // 占位。只按"最旧先丢"驱逐会先丢掉那条 final ⇒ `some(type === 'final')` 变
    // false ⇒ 回放的 turnDone 判定失效，「思考中…」卡死回归。所以驱逐必须保住
    // "最后一条 final"（与最新 terminal 一起，是最小保留集）。
    const buf = createInFlightSnapshot();
    const onlyFinal = seedPlaceholder(buf, 'final', 1);
    for (let i = 0; i < 3000; i += 1) {
      seedPlaceholder(buf, i % 2 === 0 ? 'error' : 'aborted', 2 + i);
    }
    expect(buf.events.length).toBeGreaterThan(IN_FLIGHT_MAX_EVENTS);

    pushInFlightEvent(buf, progress('p', 'stdout', 'c1', 9000));

    expectInvariants(buf);
    expect(buf.events.some((e) => e.type === 'final')).toBe(true);
    expect(buf.events.includes(onlyFinal)).toBe(true);
    // 最新 terminal（最后一条占位，aborted）同样在场。
    const terminals = buf.events.filter((e) => e.type !== 'progress');
    expect(terminals[terminals.length - 1].timestamp).toBe(2 + 2999);
  });

  it('只剩终态时条数上限不再形同虚设：未掏空的终态也按最旧驱逐，最新终态保留', () => {
    // 条数超限而字节没超：这里没有任何占位可丢，只有未掏空的终态。条数上限必须
    // 照样成立，且不该为了它去掏空任何一条（掏空不影响条数，只会白扔正文）。
    const buf = createInFlightSnapshot();
    const total = IN_FLIGHT_MAX_EVENTS + 500;
    for (let i = 0; i < total; i += 1) {
      pushInFlightEvent(buf, terminalBrief('final', i));
    }

    expectInvariants(buf);
    expect(buf.events.length).toBe(IN_FLIGHT_MAX_EVENTS);
    // 最旧的被整条驱逐，活下来的仍是原样的终态（没有为了条数上限被掏空）。
    const oldestKept = buf.events[0];
    expect((oldestKept.data as { _evicted?: boolean })._evicted).toBeUndefined();
    // 最新终态在场，watchdog 读的时间戳还在最后一条上。
    const last = buf.events[buf.events.length - 1];
    expect(last.type).toBe('final');
    expect(last.timestamp).toBe(total - 1);
  });
});
