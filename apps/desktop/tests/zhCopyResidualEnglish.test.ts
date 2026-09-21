import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Issue #1158 回归锁：中文界面不得残留裸英文按钮/状态文案。
 *
 * 背景：三处漏翻曾与周围中文界面并列显示 ——
 *   - 设置 → 模型：右上角「Refresh」按钮（旁边是「编辑当前模型」）
 *   - 启动屏：「Loading MiQroForge…」
 *   - 对话 diff 弹窗：「Loading diff...」
 *
 * 为什么用**源码文本锁**而不是运行期断言：
 * - 启动屏只在环境探测完成前出现，diff 弹窗的 loading 态只持续一次 IPC 往返 ——
 *   两者都是瞬态，e2e 要稳定抓住它们只能靠 sleep/竞态，必然引入 flake；
 * - 三处都是静态字面量，可能的回归形态就是源码里的字符串被改回去，
 *   文本锁足以覆盖，并且不用起 Electron 就能一次锁住三条路径。
 *
 * ⚠️ 若未来接入 i18n（#986 评估中）把这些字面量换成 `t('...')` 调用，
 * 请同步更新本锁 —— 届时失败是锁需要维护，不是代码有 bug。
 */

const RENDERER_CANDIDATES = [
  // vitest 通常以 apps/desktop 为 cwd 运行
  resolve(process.cwd(), 'src/renderer'),
  // 兜底：从仓库根运行
  resolve(process.cwd(), 'apps/desktop/src/renderer'),
];

const rendererRoot = RENDERER_CANDIDATES.find((candidate) => existsSync(candidate));

if (!rendererRoot) {
  throw new Error(
    `zhCopyResidualEnglish: 找不到 renderer 目录，已尝试：\n${RENDERER_CANDIDATES.join('\n')}`
  );
}

function readSource(relativePath: string): string {
  return readFileSync(join(rendererRoot as string, relativePath), 'utf8');
}

/** 渲染进程下所有 .ts/.tsx 源文件（相对 renderer 根，正斜杠分隔）。 */
function collectSources(dir = rendererRoot as string, prefix = ''): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const absolute = join(dir, entry);
    const relative = prefix ? `${prefix}/${entry}` : entry;
    if (statSync(absolute).isDirectory()) return collectSources(absolute, relative);
    return /\.tsx?$/.test(entry) ? [relative] : [];
  });
}

const sources = collectSources();
// 相对路径 → 源码文本，供「全量缺省」断言复用同一份读取结果。
const sourceByPath = new Map(sources.map((path) => [path, readSource(path)]));

/**
 * 这三条是 #1158 的原始病灶。上两条是句子式状态文案，可以直接在全量源码里判缺省；
 * 「Refresh」是个通用词（图标 import、`refreshSidebar` 等标识符都会命中），
 * 只能约束「作为 JSX 文本节点」这一种形态。
 */
const FORBIDDEN_SENTENCES = [
  { literal: 'Loading MiQroForge', site: 'App.tsx（启动屏）' },
  { literal: 'Loading diff', site: 'ChatConsole.tsx（diff 弹窗）' },
];

describe('renderer 中文界面无残留英文文案 (#1158)', () => {
  it('全量渲染进程源码内不再出现「Loading MiQroForge」「Loading diff」', () => {
    const offenders = FORBIDDEN_SENTENCES.flatMap(({ literal }) =>
      sources
        .filter((path) => sourceByPath.get(path)?.includes(literal))
        .map((path) => `${path}: ${literal}`)
    );

    expect(offenders, `发现残留英文文案：\n${offenders.join('\n')}`).toEqual([]);
  });

  it('模型页的刷新按钮文案是「刷新」，不是 JSX 文本节点 Refresh', () => {
    const source = sourceByPath.get('features/providers/ProvidersPage.tsx');
    expect(source, 'ProvidersPage.tsx 不存在，锁需要维护').toBeDefined();

    // 只匹配「标签之间纯粹是 Refresh」这一种形态，避免误伤 RefreshCw 之类的标识符。
    expect(source).not.toMatch(/>\s*Refresh\s*</);
    expect(source).toMatch(/>\s*刷新\s*</);
  });

  it('启动屏文案是「正在启动 MiQroForge…」', () => {
    const source = sourceByPath.get('App.tsx');
    expect(source, 'App.tsx 不存在，锁需要维护').toBeDefined();

    expect(source).toContain('正在启动 MiQroForge…');
  });

  it('diff 弹窗加载态文案是「正在加载差异…」', () => {
    const source = sourceByPath.get('features/chat/ChatConsole.tsx');
    expect(source, 'ChatConsole.tsx 不存在，锁需要维护').toBeDefined();

    expect(source).toContain('正在加载差异…');
  });
});
