/**
 * E2E: files.openExternal / openContainingFolder 拒绝 workspace 外路径 (#955)
 *
 * #1062 把这两个 handler 的「越界即抛异常」改成「返回结构化拒绝」
 * （`{opened:false,error}` / `{revealed:false,error}`）——渲染层会丢弃 IPC
 * rejection，用户点了没反应。安全保证没变：越界路径仍然**绝不被打开**，
 * `resolveWorkspacePath` 内部照样抛，只是被 handler 收敛成返回值。
 *
 * 本 spec 断言的是「被拒绝」这个语义，而不是它用什么形式表达：调用必须正常
 * 返回结构化结果，且该结果明确表示未打开。它不会因为失败面从异常换成返回值
 * 而失效，但仍会抓住「越界路径被真的打开了」这类真回归。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron open-external-path-security.spec.ts
 */
import { test, expect } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.skip(process.platform !== 'win32', 'Windows-only: /mnt path conversion');

test('rejects host paths outside the workspace (#955)', async () => {
  const { electronApp, page } = await launchElectronApp((config) => {
    // Force the default workspace (rebased to MIQI_HOME) so C:\Windows is
    // guaranteed outside it, regardless of the developer's config.
    if (config.agents?.defaults) delete (config.agents.defaults as any).workspace;
  });

  try {
    // Wait for the preload bridge to expose window.miqi.
    await page.evaluate(async () => {
      for (let i = 0; i < 120; i++) {
        try {
          if ((window as any).miqi?.runtime) return;
        } catch {
          /* preload not injected yet */
        }
        await new Promise((r) => setTimeout(r, 500));
      }
    });

    // 1. /mnt/c/... escape → resolveWorkspacePath refuses it → the handler
    //    reports the refusal rather than opening anything.
    const openExternal = await page.evaluate(async () => {
      try {
        return {
          ok: true as const,
          value: await (window as any).miqi.files.openExternal('/mnt/c/Windows/System32/calc.exe'),
        };
      } catch (e: any) {
        return { ok: false as const, error: String(e?.message ?? e) };
      }
    });
    expect(
      openExternal.ok,
      `openExternal must resolve to a structured refusal, got: ${JSON.stringify(openExternal)}`
    ).toBe(true);
    expect(openExternal.ok && openExternal.value?.opened).toBe(false);
    expect(String(openExternal.ok && openExternal.value?.error)).toMatch(/outside workspace/i);

    // 2. Absolute path outside workspace → same refusal, no directory shown.
    const openFolder = await page.evaluate(async () => {
      try {
        return {
          ok: true as const,
          value: await (window as any).miqi.files.openContainingFolder('C:\\Windows\\System32'),
        };
      } catch (e: any) {
        return { ok: false as const, error: String(e?.message ?? e) };
      }
    });
    expect(
      openFolder.ok,
      `openContainingFolder must resolve to a structured refusal, got: ${JSON.stringify(openFolder)}`
    ).toBe(true);
    expect(openFolder.ok && openFolder.value?.revealed).toBe(false);
    expect(String(openFolder.ok && openFolder.value?.error)).toMatch(/outside workspace/i);
  } finally {
    await closeElectronApp(electronApp);
  }
});
