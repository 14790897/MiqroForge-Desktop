/**
 * #1034 流式思考体积上界（RED→GREEN 单测）。
 *
 * 背景（测量报告 §4/§5）：60ms flush 的**频率**是有界的，但单次 flush 的
 * 代价随累计文本线性增长（放大比 ≈458 B / 1 B 文本），长思考流把渲染进程
 * 内存推到 300 MB。这里锁死两条契约：
 *
 *  1) 累积文本有硬上界：只保留尾部窗口，头部折叠成「…已省略 X 字」占位；
 *  2) 单次 flush 的代价与总长无关：保留量恒定 ⇒ 省略计数 + 保留量恒定守恒。
 *
 * 上界只作用于**流式期间的内存副本**。完整思考文本另有持久化路径
 * （`reasoning_content`：miqi/runtime/turn_runner.py:687 写助手消息、
 * miqi/runtime/history_runtime.py:148 execution_snapshots、
 * miqi/bridge/loop.py:1361 final 事件带 reasoning），turn 结束后
 * ChatConsole 的 onFinal 会用后端全量文本覆盖该 live 块。
 */
import { describe, expect, it } from 'vitest';
import {
  MAX_LIVE_REASONING_CHARS,
  appendReasoningDelta,
  dedupeReasoningBlocks,
  liveReasoningPlaceholder,
} from '../src/renderer/features/chat/ChatConsole';

/** 去掉头部占位符，拿到真正保留的尾部窗口。 */
function stripPlaceholder(text: string): string {
  const m = /^…已省略 \d+ 字\n\n/.exec(text);
  return m ? text.slice(m[0].length) : text;
}

describe('#1034 appendReasoningDelta 上界与增量累积', () => {
  it('短流不裁剪：正文原样累积，无省略占位', () => {
    let msgs = appendReasoningDelta([], '第一部分');
    msgs = appendReasoningDelta(msgs, '\n第二部分', 1, 'think');
    const live = msgs[msgs.length - 1];
    expect(live.isLiveReasoning).toBe(true);
    expect(live.reasoning).toBe('第一部分\n第二部分');
    expect(live.content).toBe('第一部分\n第二部分');
    expect(live.reasoning).not.toContain('已省略');
    expect(live.reasoningOmitted ?? 0).toBe(0);
  });

  it('超过上界后只保留尾部窗口，头部折叠为「…已省略 X 字」', () => {
    const chunk = 'x'.repeat(1000);
    let msgs = appendReasoningDelta([], chunk);
    for (let i = 0; i < 30; i += 1) msgs = appendReasoningDelta(msgs, chunk);
    const live = msgs[msgs.length - 1];
    expect(live.reasoning).toMatch(/^…已省略 \d+ 字/);
    expect(stripPlaceholder(live.reasoning ?? '').length).toBeLessThanOrEqual(
      MAX_LIVE_REASONING_CHARS
    );
    expect(live.reasoningOmitted ?? 0).toBeGreaterThan(0);
  });

  it('省略计数精确守恒：省略 + 保留 == 实际追加的字符数', () => {
    const chunk = 'ab'.repeat(500); // 1000 字符/次
    const flushes = 40;
    let msgs = appendReasoningDelta([], chunk);
    for (let i = 1; i < flushes; i += 1) msgs = appendReasoningDelta(msgs, chunk);
    const live = msgs[msgs.length - 1];
    const kept = stripPlaceholder(live.reasoning ?? '').length;
    expect((live.reasoningOmitted ?? 0) + kept).toBe(flushes * chunk.length);
  });

  it('保留的是最新文本：最旧的先被丢弃，末尾与最后一次 delta 一致', () => {
    let msgs = appendReasoningDelta([], `BEGIN${'a'.repeat(20000)}`);
    for (let i = 0; i < 20; i += 1) msgs = appendReasoningDelta(msgs, 'b'.repeat(1000));
    msgs = appendReasoningDelta(msgs, 'THE-END');
    const live = msgs[msgs.length - 1];
    expect(live.reasoning).not.toContain('BEGIN');
    expect(stripPlaceholder(live.reasoning ?? '').endsWith('THE-END')).toBe(true);
    // 占位符只出现一次（头部），不会随 flush 次数叠加
    expect((live.reasoning ?? '').match(/…已省略/g)?.length).toBe(1);
  });

  it('占位符由导出函数生成，避免测试与实现各写一份格式', () => {
    expect(liveReasoningPlaceholder(1234)).toContain('1234');
    expect(liveReasoningPlaceholder(1234).startsWith('…已省略')).toBe(true);
  });

  it('content 与 reasoning 同步，mode 与 live 标记不丢', () => {
    let msgs = appendReasoningDelta([], 'a'.repeat(9000), 1, 'fast');
    msgs = appendReasoningDelta(msgs, 'b'.repeat(9000));
    const live = msgs[msgs.length - 1];
    expect(live.content).toBe(live.reasoning);
    expect(live.reasoningMode).toBe('fast');
    expect(live.isLiveReasoning).toBe(true);
  });

  it('单条超大 delta：窗口与省略计数守恒，保留的是它的尾部', () => {
    // 后端把整段思考一次推下来（而不是逐字流）时，`prevTail + delta` 会先拼出
    // 一个与 delta 同大的临时字符串再砍掉——这里锁死裁剪发生在拼接之前：
    // 结果仍是一个常量窗口，且省略计数恰好补上被丢弃的前缀。
    const huge = `HEAD${'x'.repeat(3_000_000)}`;
    const msgs = appendReasoningDelta([], huge);
    const live = msgs[msgs.length - 1];
    const kept = stripPlaceholder(live.reasoning ?? '').length;
    expect(kept).toBeLessThanOrEqual(MAX_LIVE_REASONING_CHARS);
    expect((live.reasoningOmitted ?? 0) + kept).toBe(huge.length);
    expect(live.reasoning).not.toContain('HEAD');
    expect(live.reasoning?.endsWith(huge.slice(-100))).toBe(true);
    expect((live.reasoning ?? '').match(/…已省略/g)?.length).toBe(1);
  });

  it('单条超大 delta 之后继续 flush：前缀截断的计数不被抹掉', () => {
    const huge = 'z'.repeat(200_000);
    let msgs = appendReasoningDelta([], 'PREV');
    msgs = appendReasoningDelta(msgs, huge);
    msgs = appendReasoningDelta(msgs, 'THE-END');
    const live = msgs[msgs.length - 1];
    const kept = stripPlaceholder(live.reasoning ?? '').length;
    expect(kept).toBeLessThanOrEqual(MAX_LIVE_REASONING_CHARS);
    expect((live.reasoningOmitted ?? 0) + kept).toBe(
      'PREV'.length + huge.length + 'THE-END'.length
    );
    expect(stripPlaceholder(live.reasoning ?? '').endsWith('THE-END')).toBe(true);
  });

  it('多次 flush 后保留量恒定（单次 flush 代价与总长无关）', () => {
    let msgs = appendReasoningDelta([], 'seed');
    for (let i = 0; i < 5000; i += 1) msgs = appendReasoningDelta(msgs, 'y'.repeat(200));
    const live = msgs[msgs.length - 1];
    const kept = stripPlaceholder(live.reasoning ?? '').length;
    expect(kept).toBeLessThanOrEqual(MAX_LIVE_REASONING_CHARS);
    // 100 万字符的流：内存里只留一个常量窗口，且一字不差地守恒
    expect((live.reasoningOmitted ?? 0) + kept).toBe('seed'.length + 5000 * 200);
  });
});

describe('#1034 合并思考块后窗口重新基线化（dedupeReasoningBlocks）', () => {
  /** 后续那段思考（例如快照里同一 turn 的另一行）并进 live 块。 */
  function mergeFollowing(live: ReturnType<typeof appendReasoningDelta>[number], body: string) {
    return dedupeReasoningBlocks([
      live,
      { ...live, isLiveReasoning: false, content: body, reasoning: body },
    ]);
  }

  it('并块后继续 flush：刚并进来的文本不会被丢掉', () => {
    const live = appendReasoningDelta([], 'A'.repeat(10))[0];
    const merged = mergeFollowing(live, 'B'.repeat(10));
    expect(merged.length).toBe(1);
    expect(merged[0].content).toBe(`${'A'.repeat(10)}\n${'B'.repeat(10)}`);

    const next = appendReasoningDelta(merged, 'C')[0];
    // 未重新基线化时 tail 还停在 'A'*10，续写会把 'B'*10 抹掉
    expect(next.reasoning).toContain('B'.repeat(10));
    expect(next.reasoning?.endsWith('C')).toBe(true);
    expect(next.reasoning?.match(/…已省略/g) ?? []).toHaveLength(0);
  });

  it('并块后仍守住上界：省略 + 保留 == 合并后正文总长，占位符仍只有一个', () => {
    const live = appendReasoningDelta([], 'a'.repeat(7000))[0];
    const merged = mergeFollowing(live, 'b'.repeat(7000));
    const block = merged[0];
    const kept = stripPlaceholder(block.reasoning ?? '').length;
    expect(kept).toBeLessThanOrEqual(MAX_LIVE_REASONING_CHARS);
    expect((block.reasoningOmitted ?? 0) + kept).toBe(7000 + 1 + 7000);
    expect(block.reasoning?.match(/…已省略/g) ?? []).toHaveLength(1);
    // 窗口落在正文尾部：新并进来的 b 段还在，flush 也不丢
    expect(stripPlaceholder(block.reasoning ?? '')).toContain('b'.repeat(100));
    const next = appendReasoningDelta(merged, 'END')[0];
    expect(stripPlaceholder(next.reasoning ?? '').endsWith('END')).toBe(true);
  });
});
