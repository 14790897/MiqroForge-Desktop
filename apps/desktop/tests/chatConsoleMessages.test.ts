import { describe, expect, it } from 'vitest';
import {
  buildTaskReproContext,
  buildTaskHeaderMeta,
  buildTaskShareText,
  getTaskShareDownloadName,
  appendReasoningDelta,
  insertStandaloneReasoning,
  insertInterruptedTurns,
  wasTurnStopped,
  sessionMsgsToUi,
  toolCommandText,
  formatToolCallHint,
  applyReloginIntercept,
  RELOGIN_INTERCEPT_TEXT,
} from '../src/renderer/features/chat/ChatConsole';

describe('sessionMsgsToUi', () => {
  it('shows only the final assistant text within a tool-heavy turn', () => {
    const messages = sessionMsgsToUi([
      { role: 'user', content: 'edit the file', timestamp: '2026-07-08T01:00:00.000Z' },
      {
        role: 'assistant',
        content: 'I will update it now.',
        tool_calls: [{ function: { name: 'read_file', arguments: '{"path":"a.md"}' } }],
        timestamp: '2026-07-08T01:00:01.000Z',
      },
      {
        role: 'tool',
        name: 'read_file',
        content: 'old content',
        timestamp: '2026-07-08T01:00:02.000Z',
      },
      {
        role: 'assistant',
        content: 'The edit is complete.',
        timestamp: '2026-07-08T01:00:03.000Z',
      },
      {
        role: 'assistant',
        content: 'Final summary: updated a.md and verified the result.',
        timestamp: '2026-07-08T01:00:04.000Z',
      },
    ]);

    const assistantMessages = messages.filter((message) => message.role === 'assistant');

    expect(assistantMessages).toHaveLength(1);
    expect(assistantMessages[0].content).toBe(
      'Final summary: updated a.md and verified the result.'
    );
    expect(messages.some((message) => message.role === 'progress' && message.toolHint)).toBe(true);
  });

  it('keeps final assistant text scoped to each user turn', () => {
    const messages = sessionMsgsToUi([
      { role: 'user', content: 'first', timestamp: '2026-07-08T01:00:00.000Z' },
      { role: 'assistant', content: 'first draft', timestamp: '2026-07-08T01:00:01.000Z' },
      { role: 'assistant', content: 'first final', timestamp: '2026-07-08T01:00:02.000Z' },
      { role: 'user', content: 'second', timestamp: '2026-07-08T01:00:03.000Z' },
      { role: 'assistant', content: 'second final', timestamp: '2026-07-08T01:00:04.000Z' },
    ]);

    expect(
      messages.filter((message) => message.role === 'assistant').map((message) => message.content)
    ).toEqual(['first final', 'second final']);
  });

  it('restored tool rows show only the safe detail, never raw argument values', () => {
    const messages = sessionMsgsToUi([
      { role: 'user', content: '下载论文', timestamp: '2026-07-08T01:00:00.000Z' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          {
            function: {
              name: 'paper_download',
              arguments: '{"paperId":"An Image is Worth 16x16 Words"}',
            },
          },
        ],
        timestamp: '2026-07-08T01:00:01.000Z',
      },
      {
        role: 'tool',
        name: 'paper_download',
        arguments: { paperId: 'An Image is Worth 16x16 Words' },
        content: 'Downloaded.',
        timestamp: '2026-07-08T01:00:02.000Z',
      },
    ]);

    // One chain row per tool — the old separate hint row is gone.
    const rows = messages.filter((m) => m.role === 'progress' && m.toolHint);
    expect(rows).toHaveLength(1);
    // Detail comes from HINT_VALUE_KEYS only — paperId must not leak.
    expect(rows[0].summary).toBe('下载论文');
    expect(rows[0].summary).not.toContain('An Image is Worth 16x16 Words');
    expect(rows[0].toolOutput).toBe(true);
  });

  it('restored exec rows keep the full command in toolArgs for expansion (#902)', () => {
    // 命令长度 > 60：折叠摘要被截断，但 toolArgs 保留原文供展开使用。
    const command =
      "python -c \"import os; print([f for f in os.listdir('.') if f.endswith('.report')])\" --verbose --debug --color=never";
    const messages = sessionMsgsToUi([
      { role: 'user', content: '运行一下', timestamp: '2026-07-08T01:00:00.000Z' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [{ function: { name: 'exec', arguments: JSON.stringify({ command }) } }],
        timestamp: '2026-07-08T01:00:01.000Z',
      },
      {
        role: 'tool',
        name: 'exec',
        arguments: { command },
        content: '3',
        timestamp: '2026-07-08T01:00:02.000Z',
      },
    ]);

    const rows = messages.filter((m) => m.role === 'progress' && m.toolHint);
    expect(rows).toHaveLength(1);
    expect(rows[0].toolArgs).toEqual({ command });
    // 折叠摘要仍被截断（60 字符），完整命令只在展开区出现。
    expect(rows[0].summary).toContain('执行命令');
    expect(rows[0].summary).not.toContain(command);
    expect(rows[0].summary).toMatch(/…$/);
  });

  it('keeps reasoning as a standalone timeline block before tool calls', () => {
    const messages = sessionMsgsToUi([
      { role: 'user', content: 'think then edit', timestamp: '2026-07-08T01:00:00.000Z' },
      {
        role: 'assistant',
        content: 'I will update it now.',
        tool_calls: [{ function: { name: 'read_file', arguments: '{"path":"a.md"}' } }],
        reasoning_content: 'step one',
        timestamp: '2026-07-08T01:00:01.000Z',
      },
      {
        role: 'tool',
        name: 'read_file',
        content: 'old content',
        timestamp: '2026-07-08T01:00:02.000Z',
      },
      {
        role: 'assistant',
        content: 'Final summary: updated a.md.',
        reasoning_content: 'step one\n\n---\n\nstep two',
        timestamp: '2026-07-08T01:00:03.000Z',
      },
    ]);

    const assistantMessages = messages.filter((message) => message.role === 'assistant');
    expect(assistantMessages).toHaveLength(1);
    expect(assistantMessages[0].reasoning).toBeUndefined();

    const thinkingMessages = messages.filter(
      (message) => message.role === 'progress' && message.reasoning
    );
    expect(thinkingMessages).toHaveLength(1);
    expect(thinkingMessages[0].reasoning).toBe('step one\n\n---\n\nstep two');

    const thinkingIdx = messages.indexOf(thinkingMessages[0]);
    const toolIdx = messages.findIndex((m) => m.role === 'progress' && m.toolHint);
    const finalIdx = messages.findIndex((m) => m.role === 'assistant');
    expect(thinkingIdx).toBeGreaterThanOrEqual(0);
    expect(thinkingIdx).toBeLessThan(toolIdx);
    expect(toolIdx).toBeLessThan(finalIdx);
  });

  it('restores reasoning-only turns as a standalone thinking block', () => {
    const messages = sessionMsgsToUi([
      {
        role: 'assistant',
        content: '',
        reasoning_content: 'a long chain of thought',
        timestamp: '2026-07-08T01:00:01.000Z',
      },
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe('progress');
    expect(messages[0].reasoning).toBe('a long chain of thought');
  });

  it('restores image attachments from [Image: name] placeholders (#659)', () => {
    const messages = sessionMsgsToUi([
      {
        role: 'user',
        content: '看看这张图\n\n[Image: screenshot.png]\n[Image: chart.jpg]',
        timestamp: '2026-07-08T01:00:01.000Z',
      },
    ]);

    expect(messages).toHaveLength(1);
    const atts = messages[0].attachments ?? [];
    expect(atts).toHaveLength(2);
    expect(atts[0]).toMatchObject({
      name: 'screenshot.png',
      type: 'image',
      status: 'pending',
    });
    expect(atts[0].dataUrl).toBeUndefined(); // lazily re-read after load
    expect(atts[1].name).toBe('chart.jpg');
  });

  it('does not attach anything when the user message has no [Image:] placeholder', () => {
    const messages = sessionMsgsToUi([
      {
        role: 'user',
        content: 'plain question without images',
        timestamp: '2026-07-08T01:00:01.000Z',
      },
    ]);
    expect(messages[0].attachments).toBeUndefined();
  });
});

describe('appendReasoningDelta', () => {
  it('appends every chunk to the single live thinking bubble', () => {
    const first = appendReasoningDelta([], 'The ', 100);
    const second = appendReasoningDelta(first, 'model ', 101);
    const third = appendReasoningDelta(second, 'thinks.', 102);

    const live = third.filter((m: any) => m.isLiveReasoning);
    expect(live).toHaveLength(1);
    expect(live[0].content).toBe('The model thinks.');
  });

  it('creates a new live bubble only when none exists', () => {
    const base = [{ role: 'user' as const, content: 'hi', timestamp: 1 }];
    const result = appendReasoningDelta(base, 'step one', 200);

    expect(result.filter((m: any) => m.isLiveReasoning)).toHaveLength(1);
    expect(result[result.length - 1].content).toBe('step one');
  });

  it('never inserts a second thinking header for the same turn', () => {
    const base = [
      { role: 'user' as const, content: 'hi', timestamp: 1 },
      {
        role: 'progress' as const,
        content: 'live draft',
        reasoning: 'live draft',
        isLiveReasoning: true,
        timestamp: 2,
      },
    ];
    const result = insertStandaloneReasoning(base as any[], 'final reasoning', 9);

    const thinking = result.filter((m: any) => m.reasoning);
    expect(thinking).toHaveLength(1);
    expect(thinking[0].content).toBe('final reasoning');
    expect(thinking[0].reasoningElapsedS).toBe(9);
    expect(thinking[0].isLiveReasoning).toBe(false);
  });
});

describe('buildTaskHeaderMeta', () => {
  it('uses real file count and omits fake plugin/link placeholders', () => {
    const label = buildTaskHeaderMeta(Date.now(), 2, 3);

    expect(label).toContain('2 个文件');
    expect(label).toContain('3 个启用插件');
    expect(label).not.toContain('linked files');
    expect(label).not.toContain('Active Plugins');
  });
});

describe('buildTaskShareText', () => {
  it('builds a shareable task summary with recent messages and files', () => {
    const text = buildTaskShareText({
      title: '测试任务',
      meta: '刚刚更新 · 1 个文件',
      messages: [
        { role: 'user', content: '请修改 README', timestamp: 1 },
        { role: 'assistant', content: '已完成修改', timestamp: 2 },
        { role: 'progress', content: 'Write: README.md', timestamp: 3 },
      ],
      files: [{ path: 'README.md', name: 'README.md', op: 'edit', lastSeen: 4 }],
    });

    expect(text).toContain('# 测试任务');
    expect(text).toContain('刚刚更新 · 1 个文件');
    expect(text).toContain('- 用户: 请修改 README');
    expect(text).toContain('- MiQroForge: 已完成修改');
    expect(text).toContain('- README.md (edit)');
    expect(text).not.toContain('Write: README.md');
  });
});

describe('task share helpers', () => {
  it('builds a reproduction context with session id and full file paths', () => {
    const text = buildTaskReproContext({
      sessionKey: 'desktop:issue-243',
      title: '修复任务更新时间',
      meta: '刚刚更新 · 1 个文件',
      messages: [
        { role: 'user', content: '顶部也要显示真实文件数', timestamp: 1 },
        { role: 'assistant', content: '已接入 trackedFiles.length', timestamp: 2 },
      ],
      files: [
        {
          path: 'apps/desktop/src/renderer/features/chat/ChatConsole.tsx',
          name: 'ChatConsole.tsx',
          op: 'edit',
          lastSeen: 3,
        },
      ],
    });

    expect(text).toContain('desktop:issue-243');
    expect(text).toContain('- 用户: 顶部也要显示真实文件数');
    expect(text).toContain('[edit] apps/desktop/src/renderer/features/chat/ChatConsole.tsx');
  });

  it('sanitizes exported markdown filenames', () => {
    const name = getTaskShareDownloadName('修复: 顶部/侧边 文件?', 1783993200000);

    expect(name).toBe('修复-顶部-侧边-文件-2026-07-14T01-40-00-000Z.md');
  });
});

// ── #886: interrupted-turn preservation ────────────────────────────────────

const user = (content: string, ts: number) => ({ role: 'user', content, timestamp: ts });
const assistant = (content: string, ts: number) => ({ role: 'assistant', content, timestamp: ts });
const stopped = (ts: number) => ({ role: 'progress', content: '已停止。', timestamp: ts });

describe('wasTurnStopped', () => {
  it('returns true when the round carries the 已停止 marker', () => {
    const msgs = [user('长任务', 1), assistant('半截', 2), stopped(3)];
    expect(wasTurnStopped(msgs, 0)).toBe(true);
  });

  it('returns false for a completed round without the marker', () => {
    const msgs = [user('问题', 1), assistant('完整回答', 2)];
    expect(wasTurnStopped(msgs, 0)).toBe(false);
  });

  it('scopes to the current round only (not a later stopped round)', () => {
    const msgs = [
      user('问题A', 1),
      assistant('回答A', 2),
      user('问题B', 3),
      assistant('半截B', 4),
      stopped(5),
    ];
    expect(wasTurnStopped(msgs, 0)).toBe(false);
    expect(wasTurnStopped(msgs, 2)).toBe(true);
  });

  it('ignores a 已停止 marker that appears before the user message', () => {
    const msgs = [stopped(0), user('问题A', 1), assistant('回答A', 2)];
    expect(wasTurnStopped(msgs, 1)).toBe(false);
  });
});

describe('insertInterruptedTurns', () => {
  // timestamps are epoch-ms for messages; snapshot `updated_at` is epoch
  // seconds (the helper converts ×1000), so both land in the same domain.
  it('inserts the interrupted card after its own user message, before the retry', () => {
    const merged = [
      user('长任务', 100_000),
      user('长任务', 300_000),
      assistant('重试成功', 400_000),
    ];
    const snap = [
      {
        turn_id: 't1',
        status: 'interrupted',
        assistant_content: '半截回答',
        updated_at: 200, // → 200_000 ms, between user#1 (100k) and retry user#2 (300k)
      },
    ];
    const out = insertInterruptedTurns(merged, snap);
    expect(out.map((m) => (m.interrupted ? 'CARD' : m.role))).toEqual([
      'user',
      'CARD',
      'user',
      'assistant',
    ]);
    const card = out.find((m) => m.interrupted);
    expect(card?.content).toBe('半截回答');
    expect(card?.interruptedMeta?.turnId).toBe('t1');
  });

  it('appends at the end when the interrupted round is the latest', () => {
    const merged = [user('长任务', 100_000)];
    const snap = [
      { turn_id: 't1', status: 'interrupted', assistant_content: '半截', updated_at: 200 },
    ];
    const out = insertInterruptedTurns(merged, snap);
    expect(out.map((m) => (m.interrupted ? 'CARD' : m.role))).toEqual(['user', 'CARD']);
  });

  it('returns merged unchanged when there are no interrupted turns', () => {
    const merged = [user('a', 1), assistant('b', 2)];
    expect(insertInterruptedTurns(merged, [])).toBe(merged);
  });

  it('keeps multiple interrupted cards in chronological order', () => {
    const merged = [user('一', 100_000), user('二', 400_000), assistant('答', 500_000)];
    const snaps = [
      { turn_id: 't2', status: 'interrupted', assistant_content: '半截B', updated_at: 450 },
      { turn_id: 't1', status: 'interrupted', assistant_content: '半截A', updated_at: 200 },
    ];
    const out = insertInterruptedTurns(merged, snaps);
    const cards = out.filter((m) => m.interrupted);
    expect(cards.map((c) => c.interruptedMeta?.turnId)).toEqual(['t1', 't2']);
    expect(out.map((m) => (m.interrupted ? 'CARD' : m.role))).toEqual([
      'user',
      'CARD',
      'user',
      'CARD',
      'assistant',
    ]);
  });
});

describe('toolCommandText (issue #902)', () => {
  it('returns the command from a single args object', () => {
    expect(toolCommandText({ command: 'cp a b', timeout: 30 })).toBe('cp a b');
  });

  it('returns the first command from a merged args array', () => {
    expect(toolCommandText([{ path: 'a.md' }, { command: 'python run.py' }])).toBe('python run.py');
  });

  it('returns undefined when command is missing or not a string', () => {
    expect(toolCommandText({ path: 'a.md' })).toBeUndefined();
    expect(toolCommandText({ command: 42 })).toBeUndefined();
    expect(toolCommandText({ command: '   ' })).toBeUndefined();
    expect(toolCommandText([{ path: 'a.md' }])).toBeUndefined();
  });

  it('returns undefined for undefined/empty args', () => {
    expect(toolCommandText(undefined)).toBeUndefined();
    expect(toolCommandText(null)).toBeUndefined();
    expect(toolCommandText([])).toBeUndefined();
  });
});

describe('applyReloginIntercept (平台登录失效拦截)', () => {
  const userMsg = { role: 'user' as const, content: '继续之前的工作', timestamp: 42 };
  const interruptedCard = {
    role: 'assistant' as const,
    content: '（中断回合）',
    interrupted: true,
    timestamp: 41,
  };
  const prevMsg = { role: 'assistant' as const, content: '历史回复', timestamp: 1 };

  it('普通发送：乐观 user 气泡按时间戳匹配替换为重登引导', () => {
    const next = applyReloginIntercept([prevMsg, userMsg], userMsg, null);
    expect(next).toHaveLength(2);
    expect(next[0]).toBe(prevMsg);
    expect(next[1].role).toBe('error');
    expect(next[1].action).toBe('login');
    expect(next[1].content).toBe(RELOGIN_INTERCEPT_TEXT);
  });

  it('恢复中断回合：无乐观气泡时恢复被移除的中断卡并追加重登引导', () => {
    // handleResumeTurn 已把中断卡从列表移除，resume 路径不再推 user 气泡
    const next = applyReloginIntercept([prevMsg], userMsg, interruptedCard);
    expect(next).toHaveLength(3);
    expect(next[0]).toBe(prevMsg);
    expect(next[1]).toBe(interruptedCard);
    expect(next[2].role).toBe('error');
    expect(next[2].action).toBe('login');
    expect(next[2].content).toBe(RELOGIN_INTERCEPT_TEXT);
  });

  it('时间戳不匹配且无恢复卡片（会话已切换等）：原样返回', () => {
    const next = applyReloginIntercept([prevMsg], userMsg, null);
    expect(next).toBe(next);
    expect(next).toEqual([prevMsg]);
  });

  it('user 气泡匹配优先于恢复卡片（异常共存时按普通发送处理）', () => {
    const next = applyReloginIntercept([prevMsg, userMsg], userMsg, interruptedCard);
    expect(next).toHaveLength(2);
    expect(next[1].role).toBe('error');
  });
});
