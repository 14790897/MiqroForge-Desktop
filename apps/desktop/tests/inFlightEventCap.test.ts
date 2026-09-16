/**
 * #1034 在途事件缓存（moduleInFlightCache）计数 + 累计字节双上限（RED→GREEN）。
 *
 * 该缓存只在用户切走后接管流事件（切回时回放）。原来 `buf.events.push(...)`
 * 无上界：一个长时间思考的会话在后台可以堆下十万级事件对象，且 20 个会话
 * 各有一份。这里锁死：连续同流 delta 先折叠（**无损**，回放端按顺序拼接
 * delta，见 ChatConsole 的 exec 输出回放），折叠后仍超限则驱逐最旧的
 * progress 事件（终态事件永不驱逐）。
 */
import { describe, expect, it } from 'vitest';
import {
  IN_FLIGHT_MAX_BYTES,
  IN_FLIGHT_MAX_EVENTS,
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

  it('单条 delta 逼近字节上限时不折叠成超限巨事件', () => {
    const buf = createInFlightSnapshot();
    const half = 'q'.repeat(IN_FLIGHT_MAX_BYTES / 2 - 256);
    pushInFlightEvent(buf, progress(half));
    pushInFlightEvent(buf, progress(half));
    // 折叠会让单条事件翻倍超限 → 必须拒绝折叠（后者独立成条，不是拼成巨事件）
    expect((buf.events[buf.events.length - 1].data as { delta: string }).delta).toBe(half);
    expect(buf.events.some((e) => (e.data as { delta: string }).delta.length > half.length)).toBe(
      false
    );
    // 超限后按最旧优先驱逐，最新一条仍在
    expect(buf.events.length).toBeGreaterThanOrEqual(1);
    for (const e of buf.events) {
      expect(inFlightEventBytes(e)).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    }
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  });

  it('单流连续 merge 把记账总量推过字节上限时，合并路径同样触发回收', () => {
    const buf = createInFlightSnapshot();
    // 填充事件压在字节上限之下一点点；它不是最后一条，因此不参与折叠。
    pushInFlightEvent(buf, progress('x'.repeat(IN_FLIGHT_MAX_BYTES / 2 - 400), 'stdout', 'c1'));
    // 末尾一条很小：合并只发生在它身上，合并后单条仍远低于上限（不走拒绝分支）
    pushInFlightEvent(buf, progress('L', 'stderr', 'c2'));
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(2);

    // 同流同 tool_call_id 的 400 字 delta 合入末尾一条：记账量越界，而合并本身合法
    // （946 字节 ≪ 上限）→ 只能靠合并路径上的回收把总量拉回来。
    pushInFlightEvent(buf, progress('y'.repeat(400), 'stderr', 'c2'));
    expect(buf.bytes).toBeLessThanOrEqual(IN_FLIGHT_MAX_BYTES);
    expect(buf.events.length).toBe(1); // 最旧的 progress（填充事件）被驱逐
    expect((buf.events[0].data as { delta: string }).delta.length).toBe(401);
    expect(buf.bytes).toBe(buf.events.reduce((sum, e) => sum + inFlightEventBytes(e), 0));
  });
});
