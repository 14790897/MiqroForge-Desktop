/**
 * 生成 NSIS 卸载残留清理脚本 build/installer.nsh（issue #1177）。
 *
 * 单一事实来源：src/shared/cleanup-constants.json —— 主进程/渲染器/单测
 * 引用同一份常量（cleanup-paths.ts），本脚本在构建前把它渲染进 NSIS，
 * 保证卸载器与运行时代码的路径/名字永不漂移。
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

let out = tpl;
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
