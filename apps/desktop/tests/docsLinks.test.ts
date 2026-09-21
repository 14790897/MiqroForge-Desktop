import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  DOCS_BASE_URL,
  DOCS_TREE,
  REPO_LABEL,
  REPO_URL,
  type DocLink,
} from '../src/renderer/features/settings/docsLinks';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const mkdocsYml = readFileSync(join(repoRoot, 'mkdocs.yml'), 'utf-8');
const docsDir = join(repoRoot, 'docs');

function mkdocsScalar(key: string): string {
  const match = mkdocsYml.match(new RegExp(`^${key}:[ \\t]*(\\S+)[ \\t]*$`, 'm'));
  if (!match) throw new Error(`mkdocs.yml 缺少顶层配置 ${key}`);
  return match[1].replace(/^['"]|['"]$/g, '');
}

const allLinks: DocLink[] = DOCS_TREE.flatMap((section) => [section, ...(section.children ?? [])]);

describe('#1155 设置页「文档」链接', () => {
  it('文档站根地址取自 mkdocs.yml site_url', () => {
    expect(DOCS_BASE_URL).toBe(mkdocsScalar('site_url'));
    expect(DOCS_BASE_URL.endsWith('/')).toBe(true);
    expect(new URL(DOCS_BASE_URL).protocol).toBe('https:');
  });

  it('文档站根地址是本仓库的 Pages 路径，不再指向旧仓库 /MiQi/', () => {
    const repoName = mkdocsScalar('repo_url')
      .replace(/\.git$/, '')
      .split('/')
      .pop();
    expect(new URL(DOCS_BASE_URL).pathname).toBe(`/${repoName}/`);
    expect(DOCS_BASE_URL).not.toContain('/MiQi/');
  });

  it('章节链接都是文档站下的绝对地址', () => {
    expect(allLinks.length).toBeGreaterThan(10);
    for (const link of allLinks) {
      expect(link.href, link.label).not.toMatch(/^[a-z]+:|^\//);
      expect(link.href, link.label).not.toContain('..');
      expect(new URL(link.href, DOCS_BASE_URL).href.startsWith(DOCS_BASE_URL), link.label).toBe(
        true
      );
    }
  });

  it('章节链接在 docs/ 下有对应源文件（站点上不会是 404）', () => {
    for (const link of allLinks) {
      const page = link.href.replace(/\/$/, '');
      const candidates = [join(docsDir, `${page}.md`), join(docsDir, page, 'index.md')];
      expect(candidates.some(existsSync), `${link.label} → ${link.href} 无对应 docs 页面`).toBe(
        true
      );
    }
  });

  it('GitHub 仓库链接与展示名取自 mkdocs.yml repo_url', () => {
    expect(REPO_URL).toBe(mkdocsScalar('repo_url'));
    expect(REPO_LABEL).toBe(new URL(REPO_URL).pathname.replace(/^\//, ''));
    expect(REPO_LABEL).not.toBe('14790897/miqi');
  });
});
