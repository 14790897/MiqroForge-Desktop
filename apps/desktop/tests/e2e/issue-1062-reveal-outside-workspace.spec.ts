/**
 * #1062：结果/过程文件的「定位 / 预览」对**工作区之外**的文件此前会触发 IPC
 * rejection，渲染层丢弃后点了没反应。现在主进程统一返回结构化错误，
 * 渲染层给出可见提示。本 spec 只锁「主进程不再抛异常」这一契约。
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

// 必须是**当前平台的绝对路径**。`D:/...` 在 POSIX 上不是绝对路径，会被当成相
// 对路径锚进工作区里，于是根本不触发包含性检查（退化成 "not found"）—— 这条
// 用例在 Linux 上就失去了测安全边界的能力。
const OUTSIDE =
  process.platform === 'win32'
    ? 'D:/definitely-outside-ws/merged.pdf'
    : '/definitely-outside-ws/merged.pdf';

test.describe('#1062 工作区外附件 定位/预览', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await page.waitForTimeout(800);
  }, 60_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('openContainingFolder 返回结构化错误而不是 reject', async () => {
    const res = await page.evaluate(async (p) => {
      try {
        return {
          ok: true as const,
          value: await (window as any).miqi.files.openContainingFolder(p),
        };
      } catch (e: any) {
        return { ok: false as const, error: String(e?.message ?? e) };
      }
    }, OUTSIDE);
    console.log('[1062] reveal =', JSON.stringify(res));
    expect(res.ok).toBe(true);
    expect(res.ok && res.value?.revealed).toBe(false);
    expect(String(res.ok && res.value?.error)).toMatch(/outside workspace|not found/i);
  });

  test('openExternal 返回结构化错误而不是 reject', async () => {
    const res = await page.evaluate(async (p) => {
      try {
        return { ok: true as const, value: await (window as any).miqi.files.openExternal(p) };
      } catch (e: any) {
        return { ok: false as const, error: String(e?.message ?? e) };
      }
    }, OUTSIDE);
    console.log('[1062] openExternal =', JSON.stringify(res));
    expect(res.ok).toBe(true);
    expect(res.ok && res.value?.opened).toBe(false);
    // 断言**失败原因**，而不只是「没打开」：否则「包含性检查拒绝了它」和
    // 「文件不存在 / 别的失败」在测试里无法区分（本 PR 的边界正是前者）。
    expect(
      String(res.ok && res.value?.error),
      'must be refused by the containment check, not by some other failure'
    ).toMatch(/outside workspace/i);
  });

  test('files.read 工作区外不抛异常（返回空，UI 侧给提示）', async () => {
    const res = await page.evaluate(async (p) => {
      try {
        return { ok: true as const, value: await (window as any).miqi.files.read(p) };
      } catch (e: any) {
        return { ok: false as const, error: String(e?.message ?? e) };
      }
    }, OUTSIDE);
    console.log('[1062] read =', JSON.stringify(res));
    expect(res.ok).toBe(true);
    expect(res.ok && (res.value === null || res.value === undefined)).toBe(true);
  });
});
