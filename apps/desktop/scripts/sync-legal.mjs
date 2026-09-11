#!/usr/bin/env node
/**
 * 同步隐私协议文本到 electron-builder 的 buildResources 目录 (#837)。
 *
 * 规范文本：src/renderer/assets/legal/privacy.{zh-CN,en-US}.txt
 * （入库版本化，渲染层经 Vite ?raw 直接内联同一份文本）。
 *
 * build/ 被 .gitignore 忽略且是 electron-builder 的 NSIS 资源目录：
 * 本脚本把文本复制为 license_<语言>.txt —— electron-builder 据此自动生成
 * 按安装语言匹配的协议页（拒绝即终止安装）。打包前必须运行，已接入
 * package.json 的 build 脚本（build:win / build:mac 都会先 npm run build）。
 */
import { mkdirSync, copyFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const assetsDir = join(scriptDir, '..', 'src', 'renderer', 'assets', 'legal');
const buildDir = join(scriptDir, '..', 'build');

const FILES = [
  { from: 'privacy.zh-CN.txt', to: 'license_zh_CN.txt' },
  { from: 'privacy.en-US.txt', to: 'license_en.txt' },
];

mkdirSync(buildDir, { recursive: true });
for (const { from, to } of FILES) {
  copyFileSync(join(assetsDir, from), join(buildDir, to));
  console.log(`[sync-legal] ${from} -> build/${to}`);
}
