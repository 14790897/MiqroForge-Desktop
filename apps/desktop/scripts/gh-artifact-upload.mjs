#!/usr/bin/env node
/**
 * 把 Playwright 跑出的 JSON 报告上传到仓库固定的 `_gh-artifacts` 预发布（release assets 官方
 * API），返回它的公开下载地址，供无 secrets 的可信 job 去下载解析。
 *
 * 为什么用 release asset 而不是 actions/upload-artifact：`pull_request_target` 下
 * actions/upload-artifact 会继承 secrets-bearing 的 permissions（含 actions:write），外部 PR
 * 就能拿到任意 artifact 的下载票 —— 这和它原本要堵的那个 sink 一样严重。release asset 上传只
 * 需要 contents 权限（`pull_request_target` 的默认 token 就有），下载链接还是公开的。
 *
 * 安全：发给 GitHub API 的 Bearer token 不写进任何环境变量或输出，只出现在 -H 头里，
 * 不落进 job 日志。
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { basename } from 'node:path';

const TAG = '_gh-artifacts';

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    console.error(`缺少环境变量 ${name}`);
    process.exit(1);
  }
  return value;
}

async function api(pathname, { method = 'GET', headers = {}, body } = {}) {
  const token = requireEnv('GH_TOKEN');
  const response = await fetch(`https://api.github.com${pathname}`, {
    method,
    headers: {
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      Authorization: `Bearer ${token}`,
      ...headers,
    },
    body,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = data.message ? `：${data.message}` : '';
    throw new Error(`GitHub API ${method} ${pathname} -> ${response.status}${detail}`);
  }
  return data;
}

async function ensureRelease(repo) {
  try {
    return await api(`/repos/${repo}/releases/tags/${TAG}`);
  } catch (error) {
    if (!String(error.message).includes('404')) throw error;
  }
  return api(`/repos/${repo}/releases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tag_name: TAG,
      name: TAG,
      prerelease: true,
      // published（非 draft）：draft 的资产对匿名下载返回 404，下游 job 取不到。
      body: 'CI 产物（E2E 报告等）—— 不要删除。见 apps/desktop/scripts/gh-artifact-upload.mjs',
    }),
  });
}

function contentHash(pathname) {
  return createHash('sha256').update(readFileSync(pathname)).digest('hex').slice(0, 10);
}

async function uploadAsset(release, name, filePath) {
  const uploadBase = release.upload_url.replace(/\{.*$/, '');
  const token = requireEnv('GH_TOKEN');
  const response = await fetch(`${uploadBase}?name=${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      Authorization: `Bearer ${token}`,
    },
    body: readFileSync(filePath),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // 名字撞了（同一 hash 已存在）就复用已有资产，别重复传。
    if (response.status === 422) {
      const existing = (release.assets || []).find((asset) => asset.name === name);
      if (existing) return existing;
    }
    throw new Error(`上传资产 ${name} -> ${response.status}：${data.message || ''}`);
  }
  return data;
}

async function main() {
  const repo = requireEnv('GITHUB_REPOSITORY');
  const filePath = process.argv[2];
  const nameOverride = process.argv[3];
  if (!filePath || !existsSync(filePath)) {
    console.error(`文件不存在：${filePath}`);
    process.exit(1);
  }

  const release = await ensureRelease(repo);
  const hash = contentHash(filePath);
  const name = nameOverride || `${basename(filePath, '.json')}-${hash}.json`;
  const size = statSync(filePath).size;

  const existing = (release.assets || []).find((asset) => asset.name === name);
  const asset = existing || (await uploadAsset(release, name, filePath));

  // 只输出下载地址 —— 这是给下一步（无 secrets 的汇总 job）用的输入。
  console.log(asset.browser_download_url);
  console.error(`[gh-artifact-upload] ${name} (${size}B) ${existing ? '复用已有资产' : '已上传'}`);
}

main().catch((error) => {
  console.error(`[gh-artifact-upload] ${error.message}`);
  process.exit(1);
});
