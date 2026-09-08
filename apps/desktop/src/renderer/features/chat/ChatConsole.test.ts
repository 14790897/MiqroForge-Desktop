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
  _sha256HexOfBase64,
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
    attachments?: {
      name: string;
      type: 'image' | 'text' | 'document';
      content?: string;
      dataBase64?: string;
      contentFp?: string;
    }[]
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
    const fpa = 'a'.repeat(64);
    expect(
      _markUserTwinMatches(
        [u('(attachment)', T, [{ name: 'photo.png', type: 'image', contentFp: fpa }])],
        [u(`\n\n[Image: photo.png (fp:${fpa})]`, T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：图片内容指纹守卫——同名异字节图片不得互认、同字节可认领（CodeRabbit #969）', () => {
    const fpa = 'a'.repeat(64);
    const fpb = 'b'.repeat(64);
    // 重新生成换了图（同名异字节）：live 带 fpB，merged 只有旧副本 fpA → 不认领
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'B.png', type: 'image', contentFp: fpb }])],
        [u(`看图\n\n[Image: A.png (fp:${fpa})]`, T - 2_000)]
      )
    ).toEqual([false]);
    // 同名同字节 → 认领
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'A.png', type: 'image', contentFp: fpa }])],
        [u(`看图\n\n[Image: A.png (fp:${fpa})]`, T)]
      )
    ).toEqual([true]);
    // 同名异字节 → 不认领（CodeRabbit 主场景）
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'photo.png', type: 'image', contentFp: fpb }])],
        [u(`看图\n\n[Image: photo.png (fp:${fpa})]`, T)]
      )
    ).toEqual([false]);
    // 旧版无指纹装饰 → 不认领（无法验证内容，方向安全）
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'photo.png', type: 'image', contentFp: fpa }])],
        [u('看图\n\n[Image: photo.png]', T)]
      )
    ).toEqual([false]);
    // live 无 contentFp → 不认领（方向安全）
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'photo.png', type: 'image' }])],
        [u(`看图\n\n[Image: photo.png (fp:${fpa})]`, T)]
      )
    ).toEqual([false]);
  });

  it('#968 复核：图片名称解析向后兼容——带 (fp:…) 尾的装饰还原纯文件名', () => {
    const fpa = 'a'.repeat(64);
    const ui = sessionMsgsToUi([
      {
        role: 'user',
        content: `看图\n\n[Image: photo.png (fp:${fpa})]`,
        timestamp: '2026-09-01T00:00:00Z',
      },
    ]);
    expect(ui.find((m) => m.role === 'user')?.attachments?.[0]?.name).toBe('photo.png');
    // 旧版无指纹装饰照常解析
    const ui2 = sessionMsgsToUi([
      {
        role: 'user',
        content: '看图\n\n[Image: photo.png]',
        timestamp: '2026-09-01T00:00:00Z',
      },
    ]);
    expect(ui2.find((m) => m.role === 'user')?.attachments?.[0]?.name).toBe('photo.png');
    // 文件名含 ] + 指纹尾：以 fp 为锚反推名称，不得在名字内的 ] 截断（CodeRabbit）
    const ui3 = sessionMsgsToUi([
      {
        role: 'user',
        content: `看图\n\n[Image: IMG[1].png (fp:${fpa})]`,
        timestamp: '2026-09-01T00:00:00Z',
      },
    ]);
    expect(ui3.find((m) => m.role === 'user')?.attachments?.[0]?.name).toBe('IMG[1].png');
  });

  it('#968 复核：文档解析失败占位（[name: 大小 — parsing on server]）可剥离', () => {
    // handleSend catch 分支：冒号后先带 formatFileSize，再是 parsing on server——
    // 早期规则要求 phrase 紧跟冒号导致永不匹配（CodeRabbit 阻塞项）
    expect(
      _markUserTwinMatches([u('看')], [u('看\n\n[a.pdf: 1.2 MB — parsing on server]')])
    ).toEqual([true]);
  });

  it('#968 复核：同图真实副本可认领（内容指纹一致）', () => {
    const fpa = 'a'.repeat(64);
    expect(
      _markUserTwinMatches(
        [u('看图', T, [{ name: 'A.png', type: 'image', contentFp: fpa }])],
        [u(`看图\n\n[Image: A.png (fp:${fpa})]`, T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：文本附件内容守卫——同文本同文件名、内容不同不得互认（CodeRabbit #969）', () => {
    // 用户迭代工作流：改完 main.py 再发同文本——旧副本(print(1))认领新气泡
    // (print(2)) 会吞掉新消息，名字级校验不够，必须逐字校验嵌入内容
    expect(
      _markUserTwinMatches(
        [u('检查这个', T, [{ name: 'main.py', type: 'text', content: 'print(2)' }])],
        [u('检查这个\n\n[File: main.py]\n```\nprint(1)\n```', T)]
      )
    ).toEqual([false]);
    // 内容一致 → 认领
    expect(
      _markUserTwinMatches(
        [u('检查这个', T, [{ name: 'main.py', type: 'text', content: 'print(1)' }])],
        [u('检查这个\n\n[File: main.py]\n```\nprint(1)\n```', T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：文本附件守卫——不同文件名不认领', () => {
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'b.py', type: 'text', content: 'print(1)' }])],
        [u('看\n\n[File: a.py]\n```\nprint(1)\n```', T - 2_000)]
      )
    ).toEqual([false]);
  });

  it('#968 复核：文档附件内容守卫——同文件名内容不同不得互认（CodeRabbit #969）', () => {
    const b64v1 = btoa('hello doc v1');
    const b64v2 = btoa('hello doc v2');
    // 内容不同 → 不认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'note.txt', type: 'document', dataBase64: b64v2 }])],
        [u('看\n\n--- Document: note.txt ---\nhello doc v1\n--- End of note.txt ---', T)]
      )
    ).toEqual([false]);
    // 内容一致 → 认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'note.txt', type: 'document', dataBase64: b64v1 }])],
        [u('看\n\n--- Document: note.txt ---\nhello doc v1\n--- End of note.txt ---', T)]
      )
    ).toEqual([true]);
    // 不同文件名 → 不认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'B.pdf', type: 'document' }])],
        [u('看\n\n--- Document: A.pdf ---\n内容\n--- End of A.pdf ---', T - 2_000)]
      )
    ).toEqual([false]);
  });

  it('#968 复核：占位装饰指纹守卫——同名不同字节的不可提取附件不得互认（CodeRabbit #969）', () => {
    // 守卫只比对装饰内 (fp:…) 与 live 附件暂存的 contentFp（SHA-256 由发送前
    // 预计算写入附件），此处用合成的 64 位 hex 直接构造配对
    const fpa = 'a'.repeat(64);
    const fpb = 'b'.repeat(64);
    expect(fpa).not.toBe(fpb);
    const placeA = `看\n\n[scan.pdf: scanned PDF (fp:${fpa}) — OCR will be attempted by the server]`;
    const placeB = `看\n\n[scan.pdf: scanned PDF (fp:${fpb}) — OCR will be attempted by the server]`;
    // 指纹不同 → 不认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'scan.pdf', type: 'document', contentFp: fpb }])],
        [u(placeA, T)]
      )
    ).toEqual([false]);
    // 指纹一致 → 认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'scan.pdf', type: 'document', contentFp: fpa }])],
        [u(placeA, T)]
      )
    ).toEqual([true]);
    // 旧版无指纹占位 → 不认领（无法验证内容，方向安全）
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'scan.pdf', type: 'document', contentFp: fpa }])],
        [u('看\n\n[scan.pdf: scanned PDF — OCR will be attempted by the server]', T)]
      )
    ).toEqual([false]);
    // live 附件无 contentFp → 不认领（方向安全）
    expect(
      _markUserTwinMatches([u('看', T, [{ name: 'scan.pdf', type: 'document' }])], [u(placeA, T)])
    ).toEqual([false]);
    // 解析失败占位（带大小 + 指纹）→ 指纹一致认领
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'a.pdf', type: 'document', contentFp: fpa }])],
        [u(`看\n\n[a.pdf: 1.2 MB — parsing on server (fp:${fpa})]`, T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：占位文件名含 ] 时指纹不被截断（CodeRabbit #969 Minor）', () => {
    // 守卫切段此前从 phIdx 起找第一个 ]——文件名含 ]（report].pdf）会把段截在
    // 文件名内、丢掉 (fp:…)，合法同文件重发也被误拒 → 双显示。须从段头 + 长度起找收尾 ]。
    const fpa = 'a'.repeat(64);
    const placeA = `看\n\n[report].pdf: scanned PDF (fp:${fpa}) — OCR will be attempted by the server]`;
    // 指纹一致 → 认领（修复前因 ] 截断 seg 丢 fp 而误拒）
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'report].pdf', type: 'document', contentFp: fpa }])],
        [u(placeA, T)]
      )
    ).toEqual([true]);
    // 指纹不同 → 依旧不认领（修复不放松内容校验）
    expect(
      _markUserTwinMatches(
        [u('看', T, [{ name: 'report].pdf', type: 'document', contentFp: 'b'.repeat(64) }])],
        [u(placeA, T)]
      )
    ).toEqual([false]);
  });

  it('#968 复核：同名不可提取文档占位扫描全部出现点（CodeRabbit #969 Major）', () => {
    // 同一条消息带两张同名异字节的扫描 PDF：占位各带 (fp:…)，守卫须逐个出现点
    // 比对——旧实现只看第一个占位，第二个附件对到第一个的 fp → 误拒 → 持久化
    // 副本不被认领 → 双显示（#968 同类回归）
    const fpa = 'a'.repeat(64);
    const fpb = 'b'.repeat(64);
    const pmBoth = `看\n\n[report.pdf: scanned PDF (fp:${fpa}) — OCR will be attempted by the server]\n\n[report.pdf: scanned PDF (fp:${fpb}) — OCR will be attempted by the server]`;
    // live 两张同名附件（fpA + fpB）→ 第二张必须扫到自己的占位，整条消息认领
    expect(
      _markUserTwinMatches(
        [
          u('看', T, [
            { name: 'report.pdf', type: 'document', contentFp: fpa },
            { name: 'report.pdf', type: 'document', contentFp: fpb },
          ]),
        ],
        [u(pmBoth, T)]
      )
    ).toEqual([true]);
  });

  it('#968 复核：SHA-256 覆盖全量内容——同名同首尾、仅中段不同的大附件指纹不同', async () => {
    // CodeRabbit #969 回归要求：>8192 字符、长度与首尾 4KB 相同、中段不同的
    // dataBase64 必须产生不同指纹（采样方案会被构造性绕过，全量摘要不会）
    const s1 = 'a'.repeat(4096) + '1'.repeat(1024) + 'b'.repeat(4096); // 长度 9216
    const s2 = 'a'.repeat(4096) + '2'.repeat(1024) + 'b'.repeat(4096);
    expect(s1.length).toBe(s2.length);
    const f1 = await _sha256HexOfBase64(s1);
    const f2 = await _sha256HexOfBase64(s2);
    expect(f1).toMatch(/^[0-9a-f]{64}$/);
    expect(f1).not.toBe(f2);
    // 同一内容 → 同一指纹
    expect(await _sha256HexOfBase64(s1)).toBe(f1);
  });

  it('#968 复核：纯附件两张图 + 无文本（迭代剥离到空）', () => {
    const fpa = 'a'.repeat(64);
    const fpb = 'b'.repeat(64);
    expect(
      _markUserTwinMatches(
        [
          u('(attachment)', T, [
            { name: 'A.png', type: 'image', contentFp: fpa },
            { name: 'B.png', type: 'image', contentFp: fpb },
          ]),
        ],
        [u(`\n\n[Image: A.png (fp:${fpa})]\n\n[Image: B.png (fp:${fpb})]`, T)]
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

  it('#968 复核：未知/未来扩展附件类型——守卫默认不认领（不能仅凭 key 吞消息）', () => {
    // type 联合目前闭合于 image/text/document；若未来扩展（audio/video/archive/…）
    // 而 _persistedCoversAttachments 漏补对应分支，default 必须回落「不认领」——
    // 正文/key 完全一致时旧实现 default:true 会直接认领（吞掉真实新消息）。
    // as never 绕过闭合联合，模拟扩展后的运行时形态。
    const frontend = u('听这段', T, [{ name: 'clip.wav', type: 'audio' } as never]);
    expect(_markUserTwinMatches([frontend], [u('听这段', T)])).toEqual([false]);
  });
});
