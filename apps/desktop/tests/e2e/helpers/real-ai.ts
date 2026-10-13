/**
 * 「真实 AI」的凭据复用(E2E)。
 *
 * 本机 MiQi 的模型经「平台账号 AI 网关」(config.providers 为空):Python 端
 * 从会话工作区 `<MIQI_HOME>/workspace/.qraft/token.json` 的 `aiGateway` 块取
 * 凭据路由(miqi/providers/gateway.py,明文 JSON)。E2E 的临时工作区是空的 →
 * 把真实工作区的这份文件复制进去,真实模型即可经网关调用,环境仍完全隔离。
 * CI 上主目录里没有该文件 → 不注入,走 CI 注入的 config.json provider。
 */
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** 本机真实工作区里的网关凭据(`~/.forge` 新路径优先,回退 `~/.miqi`)。 */
export function findRealWorkspaceToken(): string | null {
  for (const base of ['.forge', '.miqi']) {
    const file = join(homedir(), base, 'workspace', '.qraft', 'token.json');
    if (existsSync(file)) return file;
  }
  return null;
}

/**
 * 把真实网关凭据复制进本轮临时 MIQI_HOME 的会话工作区。
 * 返回是否注入;注入失败(无文件/复制异常)静默跳过,由用例自身的前提判断兜底。
 */
export function seedGatewayCredentials(miqiHome: string): boolean {
  const realToken = findRealWorkspaceToken();
  if (!realToken) return false;
  try {
    const dst = join(miqiHome, 'workspace', '.qraft');
    mkdirSync(dst, { recursive: true });
    copyFileSync(realToken, join(dst, 'token.json'));
    console.log(`[test] 复用本机真实网关凭据: ${realToken}`);
    return true;
  } catch {
    return false;
  }
}
