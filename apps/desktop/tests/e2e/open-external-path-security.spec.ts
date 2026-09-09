/**
 * E2E: files.openExternal / openContainingFolder 拒绝 workspace 外路径 (#955)
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

    // 1. /mnt/c/... escape → resolveWorkspacePath throws → invoke rejects.
    const openExternal = await page.evaluate(async () => {
      try {
        const r = await (window as any).miqi.files.openExternal('/mnt/c/Windows/System32/calc.exe');
        return { threw: false, result: r };
      } catch (e: any) {
        return { threw: true, message: String(e?.message ?? e) };
      }
    });
    expect(
      openExternal.threw,
      `openExternal should reject, got: ${JSON.stringify(openExternal)}`
    ).toBe(true);

    // 2. Absolute path outside workspace → the isAbsolute+exists fast path is
    //    gone, so resolveWorkspacePath rejects it too.
    const openFolder = await page.evaluate(async () => {
      try {
        const r = await (window as any).miqi.files.openContainingFolder('C:\\Windows\\System32');
        return { threw: false, result: r };
      } catch (e: any) {
        return { threw: true, message: String(e?.message ?? e) };
      }
    });
    expect(openFolder.threw).toBe(true);
  } finally {
    await closeElectronApp(electronApp);
  }
});
