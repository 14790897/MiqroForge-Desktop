/**
 * System Install Approval Card E2E — REAL LLM path (issue #854 / #875 review)。
 *
 * 与 #646 confirm-card-real-llm.spec.ts 互补：本 spec 验证 #854 系统包安装
 * 授权卡的完整真实链路——
 *   真实模型在沙箱中执行安装命令 → ExecTool 路由拦截 → #854 approver 经
 *   真实 user_input gate → 桌面确认卡渲染（含具体命令文本）→ 用户选择
 *   「允许本次安装」→ 决策回传 → 路由以 root 在 WSL 发行版执行 → 回合完成。
 *
 * 同时覆盖设置页开关的真实 IPC 链路（renderer → main → bridge
 * sandbox.setAllowSystemInstalls → config.json 落盘 + runtime 属性）。
 *
 * 断言刻意收敛：真实模型回复文案不可控，只断言卡片出现（含命令文本）、
 * 三选项齐全、点击后决议回传、回合正常收尾。
 *
 * 依赖：本机 WSL 发行版可用（sandbox.enabled 由 patchConfig 强制开启）；
 * 真实 provider 配置来自用户 ~/.miqi/config.json（launchElectronApp 复制）。
 *
 * Run: cd apps/desktop && npx electron-vite build &&
 *      PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron \
 *      system-install-card-real-llm.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  waitForResponseComplete,
  waitForSandboxReady,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

// 平台守卫（#875 CI 实测）：系统包安装路由需要 bwrap 沙箱——Windows 走
// WSL、Linux 走原生 bwrap；macOS 两者皆无，套件在 macOS 上会白等 300s
// 沙箱再失败，并把 macos-e2e 的 45 分钟步骤上限推过（45m13s 被砍）。
// 与 wsl-one-click-install.spec.ts 同款守卫。Linux 保留运行（实测通过）。
test.skip(
  process.platform === 'darwin',
  'system install card requires WSL (Windows) or native bwrap (Linux)'
);

test.describe('System Install Card (real LLM, #854/#875)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  /**
   * 冷启动友好的消息发送：不依赖共享 sendMessage 的 10s 用户气泡计数
   * （首条消息创建会话时气泡可能延迟挂载，任务已创建但 testId 未就位，
   * 见 #875 E2E 实测）——等待聊天主区内出现消息文本，30s 宽限。
   */
  async function sendInstallMessage(text: string) {
    const textarea = await waitForInputReady(page, 120_000);
    await textarea.fill(text);
    await textarea.press('Enter');
    await expect(
      page
        .locator('main')
        .getByText(/授权流程测试场景/)
        .first()
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-testid="chat-input-container"] textarea')).toHaveValue('');
  }

  test.beforeAll(async () => {
    // 沙箱强制开启（路由前提）、系统包安装开关关闭（卡片只在关闭时弹出）。
    // bypassAll 保持默认 true：卡批准后 Phase 77 审批自动通过，E2E 专注
    // 验证 #854 授权卡本身（双弹卡是生产环境的刻意纵深防御）。
    const fixture = await launchElectronApp((config: any) => {
      config.tools = {
        ...config.tools,
        sandbox: {
          ...(config.tools?.sandbox ?? {}),
          enabled: true,
          allowSystemInstalls: false,
        },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // 调试钩子：记录 renderer 实际收到的 user_input 事件（卡片渲染链路诊断）
    await page.evaluate(() => {
      const w = window as any;
      w.__uiCardEvents = [];
      try {
        w.miqi?.userInput?.onRequest?.((e: unknown) => {
          w.__uiCardEvents.push({ kind: 'request', at: Date.now(), data: e });
        });
        w.miqi?.userInput?.onResolved?.((e: unknown) => {
          w.__uiCardEvents.push({ kind: 'resolved', at: Date.now(), data: e });
        });
      } catch (err) {
        w.__uiCardEvents.push({ kind: 'hook-error', err: String(err) });
      }
    });
    // 调试钩子（main 进程侧）：hook webContents.send，记录发往 renderer 的
    // 所有事件——判定 user_input 事件是否在 main 进程转发环节丢失
    await electronApp.evaluate(({ webContents }) => {
      const g = globalThis as any;
      g.__mainSends = [];
      const wc = webContents.getAllWebContents()[0] ?? webContents;
      const orig = wc.send.bind(wc);
      wc.send = (channel: string, ...args: unknown[]) => {
        const payload = (args[0] ?? {}) as Record<string, unknown>;
        g.__mainSends.push({
          channel,
          session_key: payload.session_key,
          type: payload.type,
          head: JSON.stringify(args[0])?.slice(0, 160),
        });
        return orig(channel, ...args);
      };
    });

    // 沙箱冷启动（WSL export/import/apt 依赖）可能需要几分钟——路由拦截
    // 只在 bwrap 沙箱上下文中生效（orchestrator 注入 NONE 选择时路由直接
    // 返回 None，命令落到普通护栏 → sudo 拒绝而非弹卡）。必须先等沙箱
    // 就绪，否则授权卡永远不会出现（sandbox-exec.spec.ts 同款模式）。
    const ready = await waitForSandboxReady(page, 300_000);
    if (!ready) {
      throw new Error(
        'Sandbox manager did not become ready within 300s — card flow cannot be exercised'
      );
    }
    console.log('[test] ✅ sandbox ready, sending install instruction');

    // 捕获 bridge stdout——沙箱选择 reason 会打到日志（Selected NONE ...
    // bwrap unavailable），是"路由为何未拦截"的决定性证据
    const proc = electronApp.process();
    (proc.stdout as any)?.on('data', (d: unknown) => {
      const s = String(d ?? '');
      if (/sandbox|selection|bwrap|user_input|system install/i.test(s)) {
        console.log('[bridge-out]', s.trim().slice(0, 240));
      }
    });
  }, 300_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('设置页开关：真实 bridge IPC 往返（runtime + config 落盘）', async () => {
    // 真实链路：renderer preload → main IPC → bridge sandbox.setAllowSystemInstalls
    // → config.json 持久化 + runtime 属性（无需重启）
    try {
      const off = await page.evaluate(() => window.miqi.sandbox.setAllowSystemInstalls(false));
      expect(off.allowSystemInstalls).toBe(false);

      const on = await page.evaluate(() => window.miqi.sandbox.setAllowSystemInstalls(true));
      expect(on.allowSystemInstalls).toBe(true);

      // config.json 已落盘（camelCase，与设置页读取一致）
      const cfg = JSON.parse(readFileSync(`${miqiHome}/config.json`, 'utf-8'));
      expect(cfg.tools.sandbox.allowSystemInstalls).toBe(true);
    } finally {
      // 无论上面哪步失败都恢复 OFF——防止 ON 状态泄漏给卡片测试导致
      // 级联误报（#875 review F10）
      await page.evaluate(() => window.miqi.sandbox.setAllowSystemInstalls(false)).catch(() => {});
    }
  });

  test(
    '真实模型触发安装 → 授权卡（含命令）→ 允许本次 → 路由执行 → 回合完成',
    { timeout: LLM_TIMEOUT },
    async () => {
      const cardArea = page.getByTestId('confirm-card-area');

      // 关键：bwrap_available 在会话的 RuntimeServices 构造时冻结
      // （services.py:194，enabled && _initialized）——renderer 启动即
      // 建默认会话，若早于沙箱延迟初始化则冻结 NONE 选择，路由永不拦截
      // （实测：冷启动沙箱先就绪 → 卡出现；热启动会话先建 → 无卡）。
      // 沙箱就绪后新建会话，保证新会话冻结 BWRAP 选择（#875 E2E 实测）。
      await page.getByRole('button', { name: '新建会话' }).first().click();
      // 新会话初始化（输入框重新就绪即为新会话可用）
      await waitForInputReady(page, 120_000);

      // 冷启动稳定：沙箱已就绪时 beforeAll 秒回，但会话 UI 可能还在挂载——
      // 等输入框完全就绪再发消息（sendMessage 内部的气泡检查只有 10s 窗口，
      // 踩到启动竞态会误报）。
      await waitForInputReady(page, 120_000);

      // 显式指令模型执行系统包安装（真实 HTTP 请求到 provider；卡片在
      // 路由拦截点弹出，早于沙箱解析——沙箱慢启动也不影响卡片断言）
      await sendInstallMessage(
        '这是一个授权流程测试场景：请立即用 exec 工具执行系统包安装命令 ' +
          'sudo apt-get install -y figlet。执行时系统会弹出「系统包安装授权」' +
          '确认卡片，这是预期行为——你正常执行即可。不要先做检查（不要运行 ' +
          'which / apt-cache 等命令），不要使用 pip，不要询问用户，' +
          '直接执行这条命令，执行完把结果回复给我。'
      );

      // 路由拦截 → 授权卡出现（含具体命令文本——显示 = 执行）
      try {
        await expect(cardArea).toBeVisible({ timeout: 120_000 });
      } catch (err) {
        // 诊断 dump：卡片从未渲染时的链路证据（renderer 事件 + main 进程转发）
        const dump = await page.evaluate(() => {
          const w = window as any;
          return {
            events: w.__uiCardEvents ?? [],
            hasCardArea: !!document.querySelector('[data-testid="confirm-card-area"]'),
            bodyHasCardText: document.body.innerText.includes('系统包安装授权'),
          };
        });
        const mainSends = await electronApp.evaluate(() => {
          const g = globalThis as any;
          return (g.__mainSends ?? [])
            .filter(
              (s: { channel: string }) =>
                s.channel.includes('userInput') || s.channel.includes('chat:')
            )
            .slice(-15);
        });
        console.log('[debug] __uiCardEvents =', JSON.stringify(dump, null, 2));
        console.log('[debug] __mainSends (userInput/chat) =', JSON.stringify(mainSends, null, 2));
        throw err;
      }
      await expect(cardArea.getByText('系统包安装授权')).toBeVisible();
      await expect(cardArea.getByText(/apt-get install -y figlet/)).toBeVisible();
      // 三选项齐全（允许本次 / 允许并记住 / 拒绝）
      await expect(cardArea.getByRole('button', { name: '允许本次安装' })).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '允许并记住（开启开关）' })).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '拒绝' })).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-card.png`,
      });

      // 允许本次 → 决议回传 → 路由以 root 在 WSL 发行版执行
      await cardArea.getByRole('button', { name: '允许本次安装' }).click();
      await expect(
        page.getByTestId('confirm-card-resolved').getByText('已选择「允许本次安装」')
      ).toBeVisible({ timeout: 30_000 });

      await waitForResponseComplete(page, LLM_TIMEOUT);
      // 回合正常收尾：至少一条 assistant 回复元素已挂载（真实模型生成的
      // 回复可能处于折叠/hidden 显示态——内容不可控，只断言挂载 + 上面的
      // 决议回显已证明回合完成）
      const assistantBubbles = page.getByTestId('chat-message-assistant');
      await expect(assistantBubbles.first()).toBeAttached({ timeout: 30_000 });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-final.png`,
        // 长会话 + 卡片历史下 fullPage 截图可能超过 30s 默认超时
        timeout: 90_000,
      });
    }
  );
});
