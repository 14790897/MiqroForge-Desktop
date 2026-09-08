/**
 * ChatConsole 回归测试（#858 → #905）。
 *
 * 回归点：fast（极速）模式也必须渲染思考块——之前 ChatConsole 用
 * `reasoningMode !== 'fast'` 把 ThinkBlock 过滤掉，导致极速模式下
 * 思考过程消失（#858）。#905 移除该门控后，测试直接覆盖渲染路径：
 * ThinkingBlockGroup 在 fast/think 两种模式下都输出思考内容。
 *
 * 门控回归防护：渲染决策收拢在 `shouldRenderThinkingGroup`（ChatConsole
 * 调用处即用它）——若有人把 fast 门控加回，该函数的测试立即失败。
 * 组件级渲染由 ThinkingBlockGroup/ThinkBlock 测试覆盖（含
 * fallbackMode='fast' 组合）；完整 ChatConsole 集成渲染依赖大量
 * window.miqi mock，成本高，由上面两个层级补齐。
 */
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ThinkingBlockGroup,
  shouldRenderThinkingGroup,
  sessionMsgsToUi,
  insertInterruptedTurns,
  _markUserTwinMatches,
} from './ChatConsole';

describe('ChatConsole thinking block regression (#858 → #905)', () => {
  it('门控决策点：fast/think 两种模式都渲染思考块组（#783 决策）', () => {
    // #858 教训：门控曾加在调用处导致 fast 模式思考过程消失。
    // 决策点恒真——任何模式都必须渲染，回归锁定。
    expect(shouldRenderThinkingGroup('fast')).toBe(true);
    expect(shouldRenderThinkingGroup('think')).toBe(true);
  });
  it('fast 模式：思考块组完整渲染（🚀 快速思考 + 内容）', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkingBlockGroup, {
        thinking: {
          reasoning: '1. 理解需求\n- 要点一',
          isLiveReasoning: true,
          reasoningMode: 'fast',
        },
        fallbackMode: 'think',
      })
    );
    expect(markup).toContain('🚀');
    expect(markup).toContain('快速思考');
    expect(markup).toContain('理解需求');
    expect(markup).toContain('要点一');
    // 头部存在
    expect(markup).toContain('MiQroForge');
  });

  it('think 模式：思考块组完整渲染（🧠 深度思考 + 内容）', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkingBlockGroup, {
        thinking: {
          reasoning: '深入分析',
          reasoningMode: 'think',
        },
        fallbackMode: 'think',
      })
    );
    expect(markup).toContain('🧠');
    expect(markup).toContain('深度思考');
    expect(markup).toContain('深入分析');
  });

  it('消息未带模式时回退到全局模式', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkingBlockGroup, {
        thinking: { reasoning: '回退模式' },
        fallbackMode: 'fast',
      })
    );
    expect(markup).toContain('快速思考');
  });

  it('历史恢复链路：后端 reasoning_mode（下划线）→ progress 行带 reasoningMode', () => {
    // #905 review P1 链路：turn_runner 以 reasoning_mode（snake_case）持久化，
    // 前端 collapseAssistantMessagesWithinTurns 必须读该字段（读驼峰
    // reasoningMode 会静默丢失，历史恢复仍回退全局模式）。
    const raw = [
      { role: 'user', content: '问题', timestamp: '2026-09-01T00:00:00Z' },
      {
        role: 'assistant',
        content: '回答',
        reasoning_content: '思考内容',
        reasoning_mode: 'fast',
        timestamp: '2026-09-01T00:00:01Z',
      },
    ];
    const ui = sessionMsgsToUi(raw);
    const thinking = ui.find((m) => m.role === 'progress' && m.reasoning);
    expect(thinking?.reasoningMode).toBe('fast');
  });

  it('历史恢复：assistant 无思考内容时也保留 reasoningMode（inline 🚀 标跟随发送模式）', () => {
    // CodeRabbit #905-3：有 content 无 reasoning_content 的 assistant 消息
    // 走 assistant 分支，reasoning_mode 必须照样映射——否则切模式后重开
    // 历史，回复的 inline 🚀/🧠 标会用全局模式显示错。
    const raw = [
      { role: 'user', content: '问题', timestamp: '2026-09-01T00:00:00Z' },
      {
        role: 'assistant',
        content: '直接回答，无思考',
        reasoning_mode: 'fast', // fast 模式发送，但模型没产出 reasoning
        timestamp: '2026-09-01T00:00:01Z',
      },
    ];
    const ui = sessionMsgsToUi(raw);
    const asst = ui.find((m) => m.role === 'assistant');
    expect(asst?.reasoning).toBeUndefined();
    expect(asst?.reasoningMode).toBe('fast');
  });

  it('中断快照恢复：reasoning_mode 贯通到卡片消息（fast 回合不显示 🧠）', () => {
    // CodeRabbit #905-4：execution_snapshots 持久化 reasoning_mode，
    // insertInterruptedTurns 必须映射——否则 fast 中断回合恢复后
    // InterruptedTurnCard 的 ThinkBlock 默认 mode='think' 显示 🧠。
    const cards = insertInterruptedTurns(
      [],
      [
        {
          turn_id: 't1',
          status: 'interrupted',
          assistant_content: '半截回答',
          reasoning_content: '思考到一半',
          reasoning_elapsed_s: 4.2,
          reasoning_mode: 'fast',
          updated_at: 1789000000,
        },
      ]
    );
    const card = cards.find((m) => m.interrupted);
    expect(card?.reasoningMode).toBe('fast');
    expect(card?.reasoningElapsedS).toBe(4);
  });
});

describe('_markUserTwinMatches 一对一去重匹配（#891 复核 + #968）', () => {
  const T = 1_700_000_000_000;
  const u = (
    content: string,
    ts = T,
    attachments?: { name: string; type: 'image' | 'text' | 'document' }[]
  ) => ({
    role: 'user' as const,
    content,
    timestamp: ts,
    ...(attachments ? { attachments: attachments.map((a) => ({ ...a, size: 0 })) } : {}),
  });

  it('纯文本：持久化副本认领同内容乐观气泡', () => {
    expect(_markUserTwinMatches([u('你好')], [u('你好')])).toEqual([true]);
  });

  it('#968 图片消息：persisted 带 [Image: …] 占位符也能互认（归一化后比对）', () => {
    // 乐观气泡 content 只有输入文本；落库 content 追加了图片占位符（handleSend payload）
    expect(
      _markUserTwinMatches([u('看看这张图')], [u('看看这张图\n\n[Image: photo.png]')])
    ).toEqual([true]);
  });

  it('#968 文件附件：persisted 带 [File: …] 代码块也能互认', () => {
    expect(
      _markUserTwinMatches([u('帮我看看')], [u('帮我看看\n\n[File: a.txt]\n```\nhello\n```')])
    ).toEqual([true]);
  });

  it('#968 文档附件：--- Document: --- 段被剥离后互认', () => {
    expect(
      _markUserTwinMatches(
        [u('解析这个 pdf')],
        [u('解析这个 pdf\n\n--- Document: report.pdf ---\n正文\n--- End of report.pdf ---')]
      )
    ).toEqual([true]);
  });

  it('内容不同不互认', () => {
    expect(_markUserTwinMatches([u('问题 A')], [u('问题 B')])).toEqual([false]);
  });

  it('#891 复核：同文本两条气泡只有最早一条被一对一认领', () => {
    // 用户 30s 内连发同一句、快照只含第一条的持久化副本——第二条必须判为
    // 未落盘（保留），不能再被同一条副本同时满足（.some() 的旧缺陷）
    expect(
      _markUserTwinMatches([u('再来一次', T), u('再来一次', T + 10_000)], [u('再来一次')])
    ).toEqual([true, false]);
  });

  it('时间相近限定：30s 外的同文本旧副本不误认', () => {
    expect(_markUserTwinMatches([u('再来一次', T)], [u('再来一次', T - 60_000)])).toEqual([false]);
  });

  it('#968 复核：正文内嵌 ``` 围栏的 [File:] 块不截断剥离（尾锚定回溯）', () => {
    // 文件内容含 ``` 行时旧惰性正则在内部围栏截断留下残留；尾锚定 + 回溯
    // 必须剥到真正的收尾围栏
    expect(
      _markUserTwinMatches(
        [u('帮我看看')],
        [u('帮我看看\n\n[File: a.py]\n```\nline1\n```\nline3\n```')]
      )
    ).toEqual([true]);
  });

  it('#968 复核：正文含 --- End of … --- 行的 Document 段不截断剥离', () => {
    expect(
      _markUserTwinMatches(
        [u('解析这个')],
        [
          u(
            '解析这个\n\n--- Document: report.pdf ---\n第一段\n--- End of report.pdf ---\n第二段\n--- End of report.pdf ---'
          ),
        ]
      )
    ).toEqual([true]);
  });

  it('#968 复核：文件名含 ] 的图片装饰（贪婪捕获回溯容忍）', () => {
    expect(_markUserTwinMatches([u('看图')], [u('看图\n\n[Image: IMG[1].png]')])).toEqual([true]);
  });

  it('#968 复核：重试回合（persisted 带 [系统提示：…] 尾）可互认', () => {
    expect(
      _markUserTwinMatches(
        [u('再来一次')],
        [u('再来一次\n\n[系统提示：这是重试请求。请换一个角度重新回答，不要复述之前的答案。]')]
      )
    ).toEqual([true]);
  });

  it('#968 复核：纯附件无文本发送（live "(attachment)" ↔ 纯装饰副本）', () => {
    expect(
      _markUserTwinMatches(
        [u('(attachment)', T, [{ name: 'photo.png', type: 'image' }])],
        [u('\n\n[Image: photo.png]', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：图片装饰名守卫——旧图副本不得认领换图后的新气泡（重新生成）', () => {
    // 重新生成换了图：live 气泡带 B.png，merged 只有旧回合 A.png 的副本。
    // key 相同（看图），但旧副本不含 [Image: B.png] → 不得认领（否则新问题被吞）
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'B.png', type: 'image' }])],
        [u('看图\n\n[Image: A.png]', T - 2_000)]
      )
    ).toEqual([false]);
  });

  it('#968 复核：文档解析失败占位（[name: 大小 — parsing on server]）可剥离', () => {
    // handleSend catch 分支：冒号后先带 formatFileSize，再是 parsing on server——
    // 早期规则要求 phrase 紧跟冒号导致永不匹配（CodeRabbit 阻塞项）
    expect(
      _markUserTwinMatches([u('看')], [u('看\n\n[a.pdf: 1.2 MB — parsing on server]')])
    ).toEqual([true]);
  });

  it('#968 复核：同图真实副本可认领（装饰名守卫通过）', () => {
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'A.png', type: 'image' }])],
        [u('看图\n\n[Image: A.png]', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：文本附件守卫——旧回合的 a.py 副本不得认领换了 b.py 的新气泡', () => {
    // key 剥离会去掉嵌入的文件内容，text 附件同文本不同文件一样碰撞 → 必须守卫
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'b.py', type: 'text' }])],
        [u('看\n\n[File: a.py]\n```\nprint(1)\n```', T - 2_000)]
      )
    ).toEqual([false]);
    // 同名同文件 → 认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'a.py', type: 'text' }])],
        [u('看\n\n[File: a.py]\n```\nprint(1)\n```', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：文档附件守卫——旧回合 A.pdf 副本不得认领换了 B.pdf 的新气泡', () => {
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'B.pdf', type: 'document' }])],
        [u('看\n\n--- Document: A.pdf ---\n内容\n--- End of A.pdf ---', T - 2_000)]
      )
    ).toEqual([false]);
    // 扫描 PDF（占位装饰 [name: …]）同名 → 认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'scan.pdf', type: 'document' }])],
        [u('看\n\n[scan.pdf: scanned PDF — OCR will be attempted by the server]', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：纯附件两张图 + 无文本（迭代剥离到空）', () => {
    expect(
      _markUserTwinMatches(
        [
          u('(attachment)', T, [
            { name: 'A.png', type: 'image' },
            { name: 'B.png', type: 'image' },
          ]),
        ],
        [u('\n\n[Image: A.png]\n\n[Image: B.png]', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：[File:] 内嵌内容以 ``` 结尾紧邻收尾围栏时仍正确剥离', () => {
    // 文件内容为 'code\n```' → 块形如 [File: x.py]\n```\ncode\n```\n```（连续两个收尾围栏）
    expect(_markUserTwinMatches([u('看')], [u('看\n\n[File: x.py]\n```\ncode\n```\n```')])).toEqual(
      [true]
    );
  });

  it('#968 + #891 复核组合：同文本两条、persisted 带图 → 归一化后仍只认领最早一条', () => {
    expect(
      _markUserTwinMatches([u('看图', T), u('看图', T + 5_000)], [u('看图\n\n[Image: a.png]', T)])
    ).toEqual([true, false]);
  });
});
