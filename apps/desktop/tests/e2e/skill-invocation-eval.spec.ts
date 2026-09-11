/**
 * Skill 精确调用量化评估（真实 LLM）
 *
 * 目的：量化 MiQroForge 自带 skills 能否被 agent 精确调用。用一组不含技能名的
 * 直接自然语言提示词（"帮我做个PPT"式），观察 agent 是否：
 *   1. 通过 skill_manage(list) 发现技能清单
 *   2. 加载了期望的 SKILL.md（skill_manage view name=X 或 read_file <skill>/SKILL.md）
 *   3. 还是加载了错误的技能 / 完全没碰技能（用内置工具直接回答）
 *
 * 观察通道：bridge 把每个工具调用的 ToolCallBeginEvent 转发为
 *   chat.onProgress({ tool_hint: true, text: <display>, tool_args: <完整参数> })
 * 判定依据（技能"被加载"）：
 *   - skill_manage 且 args.action === 'view' 且 args.name === X → 加载 X
 *   - read_file 且 args.path 以 /<X>/SKILL.md 结尾 → 加载 X
 *   - skill_manage 且 args.action === 'list' → 技能清单发现动作
 *
 * 每个用例：新建会话 → 直接发送提示词 → 轮询等待回合结束（final/error/aborted）
 * → 分析工具调用链 → 增量写入 test-results/skill-eval-report.json。
 *
 * 运行（真实 LLM，约 15-60 分钟）：
 *   cd apps/desktop && npm run build && \
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     skill-invocation-eval.spec.ts --workers=1
 * 环境变量：
 *   MIQI_SKILL_EVAL_CASES=pptx,weather  只跑部分用例
 *   MIQI_SKILL_EVAL_CASE_TIMEOUT_MIN=7  单用例超时（默认 7 分钟）
 *   MIQI_SKILL_EVAL_DEADLINE_MIN=100    整体截止（默认 100 分钟）
 *   MIQI_RUN_SKILL_EVAL=1               CI 上强制运行
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

// ─── 评估语料 ───────────────────────────────────────────────────────

interface EvalCase {
  id: string;
  /** 期望 agent 加载的技能（SKILL.md 目录名，即 skill_manage 里的 name） */
  expectedSkill: string;
  /** 直接自然语言提示词 —— 刻意不含技能名，模拟用户真实说法 */
  prompt: string;
}

const CORPUS: EvalCase[] = [
  {
    id: 'pptx',
    expectedSkill: 'pptx-generator',
    prompt:
      '帮我做一个关于「2026年AI行业趋势」的PPT，要有封面、目录、3页内容页和总结页，保存到工作区。',
  },
  {
    id: 'docx',
    expectedSkill: 'docx',
    prompt: '帮我写一份项目周报Word文档，包含本周进展、遇到的问题和下周计划三部分。',
  },
  {
    id: 'xlsx',
    expectedSkill: 'xlsx',
    prompt:
      '帮我把这几条销售数据整理成Excel表格并加上合计行：产品A 100件、产品B 200件、产品C 150件。',
  },
  {
    id: 'pdf',
    expectedSkill: 'pdf',
    prompt: '帮我生成一份关于项目进展的PDF报告，包含概述和下一步计划。',
  },
  {
    id: 'weather',
    expectedSkill: 'weather',
    prompt: '帮我查一下北京今天的气温。',
  },
  {
    id: 'cron',
    expectedSkill: 'cron',
    prompt: '帮我设置一个每天早上9点的提醒，提醒我开站会。',
  },
  {
    id: 'paper',
    expectedSkill: 'paper-research',
    prompt: '帮我搜索一篇关于金属有机框架MOF合成的最新论文。',
  },
  {
    id: 'github',
    expectedSkill: 'github',
    prompt: '帮我看看 anthropics/claude-code 这个GitHub仓库最近3天的提交记录。',
  },
  {
    id: 'summarize',
    expectedSkill: 'summarize',
    prompt:
      '帮我把下面这段话总结成3个要点：MiQroForge是一个桌面AI助手，支持代码编写、文档处理和网络研究。' +
      '它内置了技能系统，让AI可以按工作流完成复杂任务。用户可以通过聊天界面与它交互，' +
      '所有文件操作都在本地工作区完成，数据不会上传到第三方。',
  },
  {
    id: 'cleanup',
    expectedSkill: 'workspace-cleanup',
    prompt: '帮我把工作区整理一下，把生成的文件分类归档。',
  },
  {
    id: 'jobpost',
    expectedSkill: 'job-post-builder',
    prompt: '帮我写一份Java后端工程师的招聘JD，要求3年经验，熟悉Spring Boot。',
  },
  {
    id: 'taskmgmt',
    expectedSkill: 'task-management',
    prompt: '帮我把本周要办的事管起来：修登录bug、写周报、准备代码评审。',
  },
  {
    id: 'skillcreate',
    expectedSkill: 'skill-creator',
    prompt: '帮我创建一个新技能，用来每天早上自动整理工作目录里的文件。',
  },
  {
    id: 'memory',
    expectedSkill: 'memory',
    prompt: '帮我记住我的生日是3月15日。',
  },
];

// ─── 配置 ───────────────────────────────────────────────────────────

const CASE_TIMEOUT_MS = parseInt(process.env.MIQI_SKILL_EVAL_CASE_TIMEOUT_MIN || '7', 10) * 60_000;
const GLOBAL_DEADLINE_MS = parseInt(process.env.MIQI_SKILL_EVAL_DEADLINE_MIN || '100', 10) * 60_000;
const REPORT_PATH = join(__dirname, '..', '..', 'test-results', 'skill-eval-report.json');

function selectedCorpus(): EvalCase[] {
  const filter = process.env.MIQI_SKILL_EVAL_CASES;
  if (!filter) return CORPUS;
  const ids = new Set(
    filter
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
  );
  const selected = CORPUS.filter((c) => ids.has(c.id));
  return selected.length ? selected : CORPUS;
}

// ─── 工具调用分析（Node 侧，纯函数） ───────────────────────────────

interface RawToolCall {
  text: string;
  args: Record<string, unknown>;
  id: string;
}

function toolNameOf(t: RawToolCall): string {
  const m = /^([A-Za-z0-9_.-]+)/.exec(t.text || '');
  return m ? m[1] : '';
}

/** read_file 目标若是 <skill>/SKILL.md，返回技能名 */
function skillFromReadFile(t: RawToolCall): string | null {
  const p = String(t.args?.path ?? t.args?.file_path ?? '');
  if (!/skill/i.test(p)) return null;
  const m = /([^/\\]+)[/\\]SKILL\.md$/i.exec(p);
  return m ? m[1] : null;
}

interface CaseAnalysis {
  skillsLoaded: string[]; // 顺序记录每次"加载技能"动作
  firstSkill: string | null;
  discoveryCalls: number; // skill_manage list 次数
  toolChain: string[]; // 全部工具名（按调用顺序）
  wrongSkills: string[];
  expectedLoaded: boolean;
}

function analyzeTools(tools: RawToolCall[], expectedSkill: string): CaseAnalysis {
  const skillsLoaded: string[] = [];
  const toolChain: string[] = [];
  let discoveryCalls = 0;
  for (const t of tools) {
    const name = toolNameOf(t);
    if (!name) continue;
    toolChain.push(name);
    if (name === 'skill_manage') {
      if (t.args?.action === 'view' && typeof t.args?.name === 'string') {
        skillsLoaded.push(t.args.name);
      } else if (t.args?.action === 'list') {
        discoveryCalls += 1;
      }
      continue;
    }
    if (name === 'read_file') {
      const skill = skillFromReadFile(t);
      if (skill) skillsLoaded.push(skill);
    }
  }
  const expectedLoaded = skillsLoaded.includes(expectedSkill);
  const wrongSkills: string[] = [];
  for (const s of skillsLoaded) {
    if (s !== expectedSkill && !wrongSkills.includes(s)) wrongSkills.push(s);
  }
  return {
    skillsLoaded,
    firstSkill: skillsLoaded[0] ?? null,
    discoveryCalls,
    toolChain,
    wrongSkills,
    expectedLoaded,
  };
}

type Verdict = 'hit' | 'wrong_skill' | 'no_skill' | 'error' | 'timeout';

function decideVerdict(a: CaseAnalysis, terminal: { kind: string } | null): Verdict {
  if (!terminal) return 'timeout';
  if (terminal.kind !== 'final') return 'error';
  if (a.expectedLoaded) return 'hit';
  if (a.skillsLoaded.length > 0) return 'wrong_skill';
  return 'no_skill';
}

// ─── 测试主体 ───────────────────────────────────────────────────────

test.describe('Skill 精确调用量化评估（真实 LLM）', () => {
  test.skip(
    !!process.env.CI && process.env.MIQI_RUN_SKILL_EVAL !== '1',
    '真实 LLM 评估；CI 上需 MIQI_RUN_SKILL_EVAL=1 才会运行（本地默认运行）。'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
    // 双保险：配置已 bypass_all，再挂通配预授权
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('skill-invocation-eval', async () => {
    test.setTimeout(0); // 用内部全局截止控制，避免 worker 中途被杀
    const corpus = selectedCorpus();
    console.log(`[skill-eval] 语料 ${corpus.length} 条，单用例超时 ${CASE_TIMEOUT_MS / 60000} min`);

    // 一次性订阅进度/终态事件，写入 window.__skillEval（每用例前重置）
    await page.evaluate(() => {
      const w = window as any;
      if (!w.__skillEvalSubscribed) {
        w.__skillEvalSubscribed = true;
        w.miqi.chat.onProgress((d: any) => {
          const s = w.__skillEval;
          if (!s) return;
          if (d?.tool_hint && (d?.tool_call_id || d?.text)) {
            s.tools.push({ text: d.text || '', args: d.tool_args || {}, id: d.tool_call_id });
          }
        });
        w.miqi.chat.onFinal((d: any) => {
          if (w.__skillEval) w.__skillEval.terminal = { kind: 'final', content: d?.content || '' };
        });
        w.miqi.chat.onError((d: any) => {
          if (w.__skillEval) w.__skillEval.terminal = { kind: 'error', message: d?.message || '' };
        });
        w.miqi.chat.onAborted((d: any) => {
          if (w.__skillEval) w.__skillEval.terminal = { kind: 'aborted', reason: d?.reason || '' };
        });
        // 无人值守评估：agent 主动发起的人机握手确认卡（ask_user_confirm_card,
        // issue #646/#711）自动应答"确认"——模拟真实用户点击确认，让任务继续。
        // 只选 role 非 cancel/adjust 的选项；没有 role 信息时取第一个。
        w.miqi.userInput.onRequest((d: any) => {
          const choices: Array<{ id: string; label: string; role?: string }> = d?.choices || [];
          const pick =
            choices.find((c) => c.role !== 'cancel' && c.role !== 'adjust') ?? choices[0];
          if (!pick || !d?.input_id) return;
          w.__skillEval?.cards.push({ title: d?.title, picked: pick.label });
          w.miqi.userInput.resolve(d.input_id, pick.id, pick.label, false);
        });
      }
    });

    const results: Array<Record<string, unknown>> = [];
    const globalDeadline = Date.now() + GLOBAL_DEADLINE_MS;

    for (const c of corpus) {
      if (Date.now() >= globalDeadline) {
        console.log('[skill-eval] 达到整体截止，跳过剩余用例');
        for (const rest of corpus.slice(corpus.indexOf(c))) {
          results.push({
            id: rest.id,
            expectedSkill: rest.expectedSkill,
            verdict: 'timeout',
            note: 'global deadline',
          });
        }
        break;
      }

      console.log(`\n[skill-eval] ── 用例 ${c.id} (期望技能: ${c.expectedSkill}) ──`);
      console.log(`[skill-eval] prompt: ${c.prompt.slice(0, 80)}`);

      // 用例准备（cleanup 用例播种几个杂项文件，让"清理"有对象）
      if (c.id === 'cleanup') {
        const wsDir = join(miqiHome, 'workspace');
        mkdirSync(wsDir, { recursive: true });
        for (const f of [
          'old_logs.txt',
          'temp_notes.md',
          'draft_backup_copy.txt',
          'meeting_notes_202601.md',
        ]) {
          if (!existsSync(join(wsDir, f)))
            writeFileSync(join(wsDir, f), `# ${f}\n\n评估播种的临时文件。\n`);
        }
      }

      // 新建会话（隔离上下文），拿到 session key
      await createNewConversation(page);
      const sessionKey = await page.evaluate(
        () => localStorage.getItem('miqi:lastSession') || 'desktop:default'
      );

      // 重置收集器并发送
      await page.evaluate(() => {
        (window as any).__skillEval = { tools: [], cards: [], terminal: null, t0: Date.now() };
      });
      await page.evaluate(
        ([text, key]: [string, string]) => (window as any).miqi.chat.send(text, key),
        [c.prompt, sessionKey]
      );

      // 轮询终态
      const caseDeadline = Date.now() + CASE_TIMEOUT_MS;
      let state: {
        tools: RawToolCall[];
        cards: Array<{ title: string; picked: string }>;
        terminal: { kind: string; content?: string; message?: string; reason?: string } | null;
      } | null = null;
      while (Date.now() < caseDeadline) {
        state = await page.evaluate(() => {
          const s = (window as any).__skillEval;
          return s?.terminal
            ? { tools: [...s.tools], cards: [...(s.cards || [])], terminal: s.terminal }
            : null;
        });
        if (state) break;
        await page.waitForTimeout(2000);
      }

      const tools = state?.tools ?? [];
      const cards = state?.cards ?? [];
      const terminal = state?.terminal ?? null;
      const analysis = analyzeTools(tools, c.expectedSkill);
      const verdict = decideVerdict(analysis, terminal);

      const durationMs = await page.evaluate(() => {
        const s = (window as any).__skillEval;
        return s ? Date.now() - (s.t0 || Date.now()) : 0;
      });

      const entry: Record<string, unknown> = {
        id: c.id,
        expectedSkill: c.expectedSkill,
        prompt: c.prompt,
        verdict,
        skillsLoaded: analysis.skillsLoaded,
        firstSkill: analysis.firstSkill,
        wrongSkills: analysis.wrongSkills,
        discoveryCalls: analysis.discoveryCalls,
        toolCount: tools.length,
        toolChain: analysis.toolChain,
        confirmCards: cards,
        durationMs,
        terminalKind: terminal?.kind ?? 'none',
        finalSnippet: String(
          terminal?.content ?? terminal?.message ?? terminal?.reason ?? ''
        ).slice(-200),
      };
      results.push(entry);

      const flag =
        verdict === 'hit'
          ? '✅'
          : verdict === 'wrong_skill'
            ? '⚠️'
            : verdict === 'no_skill'
              ? '❌'
              : '⏱️';
      console.log(
        `[skill-eval] ${flag} ${c.id}: verdict=${verdict}, ` +
          `skillsLoaded=[${analysis.skillsLoaded.join(', ') || '无'}], ` +
          `discovery=${analysis.discoveryCalls}, tools=${tools.length}, ` +
          `${Math.round(durationMs / 1000)}s`
      );
      console.log(`[skill-eval] toolChain: ${analysis.toolChain.slice(0, 10).join(' → ')}`);

      // 增量落盘（防 worker 被超时杀掉丢结果）
      writeReport(results);
    }

    // ── 汇总 ─────────────────────────────────────────────────────────
    const counts = { hit: 0, wrong_skill: 0, no_skill: 0, error: 0, timeout: 0 };
    for (const r of results) counts[r.verdict as keyof typeof counts] += 1;
    const total = results.length;
    const recall = total ? counts.hit / total : 0;
    // 精确率：在"碰了技能"的回合中，加载了期望技能的比例
    const skillTouching = counts.hit + counts.wrong_skill;
    const precision = skillTouching ? counts.hit / skillTouching : 0;
    const discoveryTurns = results.filter((r) => (r.discoveryCalls as number) > 0).length;

    const summary = {
      total,
      hit: counts.hit,
      wrongSkill: counts.wrong_skill,
      noSkill: counts.no_skill,
      error: counts.error,
      timeout: counts.timeout,
      /** 召回率：期望技能被加载的回合占比 */
      recall: Number(recall.toFixed(3)),
      /** 精确率：碰了技能的回合里加载正确技能的占比 */
      precision: Number(precision.toFixed(3)),
      /** 发现率：至少调用一次 skill_manage(list) 的回合占比 */
      discoveryRate: Number((total ? discoveryTurns / total : 0).toFixed(3)),
    };

    writeReport(results, summary);

    console.log('\n[skill-eval] ══════════ 汇总 ══════════');
    console.log(`[skill-eval] 用例总数: ${total}`);
    console.log(`[skill-eval] ✅ 精确命中: ${counts.hit}`);
    console.log(`[skill-eval] ⚠️ 错误技能: ${counts.wrong_skill}`);
    console.log(`[skill-eval] ❌ 未用技能: ${counts.no_skill}`);
    console.log(`[skill-eval] ⏱️ 错误/超时: ${counts.error} / ${counts.timeout}`);
    console.log(`[skill-eval] 召回率(期望技能被加载): ${(recall * 100).toFixed(1)}%`);
    console.log(`[skill-eval] 精确率(碰技能时选对): ${(precision * 100).toFixed(1)}%`);
    console.log(
      `[skill-eval] 清单发现率(skill_manage list): ${(summary.discoveryRate * 100).toFixed(1)}%`
    );
    console.log(`[skill-eval] 报告: ${REPORT_PATH}`);

    // 评估本身不硬性失败（数据驱动观察），但打印未命中清单便于定位
    for (const r of results) {
      if (r.verdict !== 'hit') {
        console.log(
          `[skill-eval] 未命中 ${r.id}: verdict=${r.verdict} firstSkill=${r.firstSkill} ` +
            `skillsLoaded=[${(r.skillsLoaded as string[]).join(', ')}] ` +
            `final="${(r.finalSnippet as string).replace(/\s+/g, ' ').slice(-120)}"`
        );
      }
    }
    // 评估结论不阻塞 CI（报告文件是产出物）
    expect(true).toBe(true);

    function writeReport(cases: Array<Record<string, unknown>>, sum?: typeof summary) {
      const report = sum
        ? { generatedAt: new Date().toISOString(), summary: sum, cases }
        : { cases };
      mkdirSync(join(__dirname, '..', '..', 'test-results'), { recursive: true });
      writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2));
    }
  });
});
