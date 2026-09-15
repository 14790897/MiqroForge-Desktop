#!/usr/bin/env node
/**
 * 同步法律文件文本到 electron-builder 的 buildResources 目录 (#837 / #1068)。
 *
 * 规范文本：src/renderer/assets/legal/*.zh-CN.md（律师定稿，入库版本化，
 * 渲染层经 Vite ?raw 直接内联同一份文本）。
 *
 * build/ 被 .gitignore 忽略且是 electron-builder 的 NSIS 资源目录：本脚本把
 * 《用户协议》+《隐私政策》合并、去掉 Markdown 结构标记后写为 license_<语言>.txt
 * —— electron-builder 据此自动生成按安装语言匹配的协议页（拒绝即终止安装）。
 *
 * 法律文本仅中文：安装器各语言版本共用中文正文（中文版为唯一权威版本）。
 * 打包前必须运行，已接入 package.json 的 build 脚本（build:win / build:mac
 * 都会先 npm run build）。
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const assetsDir = join(scriptDir, '..', 'src', 'renderer', 'assets', 'legal');
const buildDir = join(scriptDir, '..', 'build');

const SOURCES = ['terms.zh-CN.md', 'privacy.zh-CN.md'];
const TARGETS = ['license_zh_CN.txt', 'license_en.txt'];

/** Markdown -> 安装器协议页纯文本：去标题井号、去表格分隔行、表格行去首尾竖线。 */
function toPlainText(markdown) {
  const lines = markdown.split(/\r?\n/).map((line) => {
    const trimmed = line.trim();
    if (/^\|[\s\-|]+\|$/.test(trimmed)) return null;
    let out = trimmed.replace(/^#{1,6}\s+/, '');
    if (/^\|.*\|$/.test(out)) {
      out = out.replace(/^\|\s?/, '').replace(/\s?\|$/, '');
    }
    return out;
  });
  const text = lines
    .filter((line) => line !== null)
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return text + '\n';
}

const merged = SOURCES.map((name) => toPlainText(readFileSync(join(assetsDir, name), 'utf8'))).join(
  '\n\n'
);

mkdirSync(buildDir, { recursive: true });
for (const to of TARGETS) {
  writeFileSync(join(buildDir, to), merged);
  console.log(`[sync-legal] ${SOURCES.join(' + ')} -> build/${to} (${merged.length} chars)`);
}
