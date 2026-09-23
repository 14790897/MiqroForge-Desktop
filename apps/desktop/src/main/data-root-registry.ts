/**
 * 打包版启动时把实际数据根写入 HKCU\Software\MiqroForge\DataRoot，
 * 供 NSIS 卸载器勾选「删除应用数据」时定位并清除残留（issue #1177）。
 *
 * best-effort：写失败只记日志——卸载器会退回默认名候选探测
 * （~/.forge | ~/.miqi | ~/.assistant，见 cleanup-paths.ts），
 * 仅 MIIQI_HOME 自定义数据根且注册表缺失的场景会跳过数据根清理（宁可漏删）。
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import {
  REGISTRY_KEY_PATH,
  REGISTRY_VALUE_DATA_ROOT,
  resolveActiveDataRoot,
  type CleanupContext,
} from '../shared/cleanup-paths';

export function computeRegistryDataRoot(opts?: {
  homeDir?: string;
  env?: Record<string, string | undefined>;
  dirExists?: (dir: string) => boolean;
}): string {
  const platform =
    process.platform === 'win32'
      ? 'win32'
      : process.platform === 'darwin'
        ? 'darwin'
        : process.platform === 'linux'
          ? 'linux'
          : 'other';
  const ctx: CleanupContext = {
    platform,
    homeDir: opts?.homeDir ?? homedir(),
    // 以下两项仅参与安全校验（本函数用不到），占位即可。
    appDataDir: '',
    localAppDataDir: '',
    registryDataRoot: null,
    env: opts?.env ?? process.env,
    dirExists: opts?.dirExists ?? existsSync,
  };
  // 镜像 Python 侧 miqi/utils/helpers.py get_data_path() 的解析顺序；
  // #1175 把 Python 默认名改为 .forge 时同步改 cleanup-constants.json。
  return resolveActiveDataRoot(ctx);
}

/** 返回 true 表示 reg.exe 写入成功（reg.exe 不存在或失败都返回 false）。 */
export function writeDataRootToRegistry(root: string): boolean {
  if (process.platform !== 'win32') return false;
  try {
    const r = spawnSync(
      'reg.exe',
      [
        'ADD',
        `HKCU\\${REGISTRY_KEY_PATH}`,
        '/v',
        REGISTRY_VALUE_DATA_ROOT,
        '/t',
        'REG_SZ',
        '/d',
        root,
        '/f',
      ],
      { windowsHide: true, timeout: 10000 }
    );
    return r.status === 0;
  } catch {
    return false;
  }
}
