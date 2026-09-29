/**
 * 生成 electron-builder 的自定义 NSIS 脚本 build/installer.nsh。
 *
 * 该文件是**生成产物**，由两份源码拼成（顺序不可换）：
 *   1. scripts/installer-dirpage.nsh —— 安装期：目录页校验（issue #1176），
 *      依赖 !define MUI_PAGE_CUSTOMFUNCTION_LEAVE 早于 MUI_PAGE_DIRECTORY 展开。
 *   2. scripts/installer.nsh.tpl     —— 卸载期：残留清理（issue #1177），
 *      占位符由 src/shared/cleanup-constants.json 渲染，保证卸载器与运行时代码
 *      的路径/名字永不漂移。
 *
 * 两份脚本各自有 !ifndef / !ifdef BUILD_UNINSTALLER 守卫，互不干扰；
 * 但 electron-builder 的 nsis.include 只能指一个文件，必须合并输出。
 *
 * 输出带 UTF-8 BOM：makensis 无 BOM 时按 ANSI 码页解读，中文会乱码。
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const constants = JSON.parse(
  readFileSync(path.join(rootDir, 'src/shared/cleanup-constants.json'), 'utf8')
);
const dirPage = readFileSync(path.join(rootDir, 'scripts/installer-dirpage.nsh'), 'utf8');
const tpl = readFileSync(path.join(rootDir, 'scripts/installer.nsh.tpl'), 'utf8');

// NSIS 标签不能含点号/非标识符字符，候选目录名需要清洗后做标签 id。
const idForName = (name) => name.replace(/^\./, '').replace(/[^a-z0-9]/gi, '_');

const candidateRmLines = constants.dataRootCandidateNames
  .map((name) => {
    const id = idForName(name);
    const legacy = name === '.assistant' ? '（旧版）' : '';
    return `    !insertmacro CleanupRemoveDir "$PROFILE\\${name}" "数据根 ${name}${legacy}" ${id}`;
  })
  .join('\n');

const tokens = {
  '@@DISTRO@@': constants.wslSandboxDistro,
  '@@REG_KEY@@': constants.registryKeyPath,
  '@@REG_VALUE@@': constants.registryValueDataRoot,
  '@@USERDATA_DIR@@': constants.packagedUserDataDirName,
  '@@UPDATER_DIR@@': constants.updaterCacheDirName,
  '@@ACTIVE_DEFAULT@@': constants.activeDataRootDefaultName,
  '@@CANDIDATE_RM_LINES@@': candidateRmLines,
};

// 目录页脚本在前（见文件头注释的顺序约束），卸载清理模板在后。
let out = `${dirPage.trimEnd()}\n\n${tpl}`;
for (const [token, value] of Object.entries(tokens)) {
  out = out.replaceAll(token, value);
}

const leftover = out.match(/@@[A-Z_]+@@/g);
if (leftover) {
  console.error(`[generate-uninstaller-nsh] 未替换的占位符: ${leftover.join(', ')}`);
  process.exit(1);
}

// BOM：见文件头注释。
const outPath = path.join(rootDir, 'build/installer.nsh');
writeFileSync(outPath, '﻿' + out, 'utf8');
console.log(`[generate-uninstaller-nsh] 已生成 ${outPath}`);
