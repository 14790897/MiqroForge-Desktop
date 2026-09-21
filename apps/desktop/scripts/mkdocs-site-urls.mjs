// mkdocs.yml 是文档站的唯一事实来源：deploy-docs.yml 用它构建并发布站点。
// 桌面端要链接的地址也在构建期从这里读取，避免仓库/域名变更后前端再次漂移（#1155）。
import { readFileSync } from 'node:fs';

/**
 * 从仓库根 mkdocs.yml 读取文档站与仓库地址。
 *
 * @param {string} mkdocsPath mkdocs.yml 的绝对路径
 * @returns {{ docsBaseUrl: string, repoUrl: string }}
 */
export function readSiteUrlSources(mkdocsPath) {
  let raw;
  try {
    raw = readFileSync(mkdocsPath, 'utf-8');
  } catch (err) {
    throw new Error(`未能读取 mkdocs.yml（${mkdocsPath}）：${err.message}`);
  }

  const docsBaseUrl = readScalar(raw, 'site_url');
  const repoUrl = readScalar(raw, 'repo_url');
  if (!docsBaseUrl || !repoUrl) {
    throw new Error(`mkdocs.yml 缺少 site_url / repo_url 顶层配置：${mkdocsPath}`);
  }
  return { docsBaseUrl, repoUrl };
}

/**
 * 读取顶层 YAML 标量（`key: value`），去掉可选引号。
 *
 * @param {string} raw
 * @param {string} key
 * @returns {string | null}
 */
function readScalar(raw, key) {
  const match = raw.match(new RegExp(`^${key}:[ \\t]*(\\S+)[ \\t]*$`, 'm'));
  return match ? match[1].replace(/^['"]|['"]$/g, '') : null;
}
