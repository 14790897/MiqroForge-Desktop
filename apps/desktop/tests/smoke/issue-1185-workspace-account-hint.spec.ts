import { expect, test, type Page } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/**
 * #1185：工作目录字段的两种语义要在设置页就地讲清楚 —— 默认目录按登录账号
 * 隔离，用户自己指定的目录不隔离（同设备其它账号可见）。
 *
 * 这条提示是「自定义工作区保留原样、不按账号收口」这个决定的对外出口：没有
 * 它，用户把工作目录指到共享盘时不会知道其它账号能看到里面的内容。
 */
async function openGeneralTab(page: Page, workspace: string) {
  await page.addInitScript({
    content: buildMockBridgeScript({
      config: { agents: { defaults: { name: 'miqi', workspace } } },
    }),
  });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.getByText(/^(System Settings|系统设置)$/).click();
}

/** 工作目录字段连同它下面的提示行（供截图取证）。 */
function workspaceField(page: Page) {
  return page
    .getByPlaceholder('~/.miqi/workspace')
    .locator('xpath=ancestor::div[contains(@class,"flex flex-col gap-1.5")][1]');
}

test('issue #1185: 默认工作目录提示按登录账号隔离', async ({ page }) => {
  await openGeneralTab(page, '~/.miqi/workspace');

  await expect(page.getByPlaceholder('~/.miqi/workspace')).toHaveValue('~/.miqi/workspace');
  await expect(page.getByText(/按登录账号隔离/)).toBeVisible();
  await expect(page.getByText(/其它账号也能看到/)).toHaveCount(0);

  await workspaceField(page).screenshot({
    path: 'test-reports/issue-1185-workspace-default.png',
  });
});

test('issue #1185: 自定义工作目录提示不隔离', async ({ page }) => {
  await openGeneralTab(page, 'D:/shared-project');

  await expect(page.getByPlaceholder('~/.miqi/workspace')).toHaveValue('D:/shared-project');
  await expect(page.getByText(/其它账号也能看到/)).toBeVisible();
  await expect(page.getByText(/按登录账号隔离/)).toHaveCount(0);

  await workspaceField(page).screenshot({
    path: 'test-reports/issue-1185-workspace-custom.png',
  });
});
