/**
 * mock OpenAI server 启动 helper —— plan-card / auto-timeline 共享
 * （CodeRabbit 8-24 nitpick：原两处 byte-identical 副本合并）。
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { createConnection } from 'node:net';
import { join } from 'node:path';
import { APPS_DESKTOP } from './electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

export async function waitForTcpListener(port: number): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await new Promise<void>((resolve) => {
      const socket = createConnection({ host: '127.0.0.1', port });
      const finish = () => {
        socket.removeAllListeners();
        socket.destroy();
        resolve();
      };
      socket.once('connect', finish);
      socket.once('error', finish);
      socket.setTimeout(1000, finish);
    });
    // A successful TCP connect is a direct readiness signal; unlike a
    // child-process stdout pipe it does not depend on Python stdout delivery.
    // Re-probe until the server is actually accepting connections.
    const probe = await new Promise<boolean>((resolve) => {
      const socket = createConnection({ host: '127.0.0.1', port });
      const ok = () => {
        socket.destroy();
        resolve(true);
      };
      const fail = () => {
        socket.destroy();
        resolve(false);
      };
      socket.once('connect', ok);
      socket.once('error', fail);
      socket.setTimeout(1000, fail);
    });
    if (probe) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `mock OpenAI server did not accept TCP connections on 127.0.0.1:${port} within 30s`
  );
}

export async function startMockOpenAI(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  // macOS 无 'python' 别名（只有 python3）——POSIX 平台用 python3，
  // Windows 用 python；MIQI_PYTHON_PATH 始终优先。
  const python =
    process.env.MIQI_PYTHON_PATH || (process.platform === 'win32' ? 'python' : 'python3');
  // 前置诊断：python 是否可执行（macos CI 上 mock 起不来的根因定位）
  {
    const probe = spawnSync(python, ['-c', 'import sys; print(sys.version)'], {
      encoding: 'utf-8',
      timeout: 15_000,
    });
    console.log(
      `[test] python probe (${python} @ ${process.platform}/${process.arch}): ` +
        `status=${probe.status} out=${(probe.stdout || '').trim()} ` +
        `err=${(probe.stderr || '').trim().slice(0, 300)} error=${probe.error?.message ?? ''}`
    );
  }
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_openai.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });
  let stderrTail = '';
  proc.on('error', (err: NodeJS.ErrnoException) => {
    // spawn 失败（如 ENOENT: python 不存在）不会走 exit——必须显式记录
    console.log(`[test] mock server spawn error: ${err?.code ?? ''} ${err?.message ?? err}`);
  });
  proc.stdout?.on('data', (d) => console.log(`[mock] ${String(d).trim()}`));
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock server exited: ${code}`));

  const startupDeadline = Date.now() + 30_000;
  while (proc.exitCode === null && Date.now() < startupDeadline) {
    try {
      await waitForTcpListener(port);
      const mockUrl = `http://127.0.0.1:${port}/v1`;
      console.log(`[test] mock OpenAI server ready at ${mockUrl}`);
      return { proc, mockUrl };
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  if (proc.exitCode !== null) {
    throw new Error(`mock OpenAI server exited early (code ${proc.exitCode}): ${stderrTail}`);
  }
  proc.kill();
  throw new Error(`mock OpenAI server did not become ready in 30s: ${stderrTail}`);
}

/**
 * 门禁适配（#1000/#1025 登录/模型门）：发送拦截检查 active_model_resolvable——
 * 默认模型必须能路由到可用 provider。本机/CI 的 config.providers 可能为空、
 * agents.defaults.model 指向无凭据的 provider——显式注入 mock provider 并把
 * 默认模型切到它（deepseek/deepseek-chat，OpenAI 兼容 → mock 支持）。
 */
export function patchConfigForMock(config: any, mockUrl: string): void {
  const providers = config.providers ?? {};
  for (const [, provider] of Object.entries(providers)) {
    if (provider && typeof provider === 'object') {
      (provider as any).apiBase = mockUrl;
      if (!(provider as any).apiKey) (provider as any).apiKey = 'mock-key';
    }
  }
  (providers as any).deepseek = {
    ...((providers as any).deepseek ?? {}),
    apiBase: mockUrl,
    apiKey: 'mock-key',
  };
  config.providers = providers;
  config.agents = config.agents ?? {};
  config.agents.defaults = config.agents.defaults ?? {};
  config.agents.defaults.model = 'deepseek/deepseek-chat';
}
