import { defineConfig } from '@playwright/test';

/**
 * 云端登录 live E2E 专用配置（真实平台，需 QRAFT_PHONE / QRAFT_PASSWORD）。
 *
 * 与 playwright.config.ts 的 electron 项目差异：
 *  - 只跑云端登录相关 spec（其余用例走主配置的 mock/本地链路）；
 *  - **关闭录屏/截图/追踪**：授权窗口里会真实输入账号手机号，而 CI 产物
 *    （artifact / report）对公开仓库读者可下载 —— 不能把账号信息带出去；
 *  - retries 0：任何自动重试都可能产出带凭据上下文的诊断产物，不值得。
 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: ['qraft-browser-login.spec.ts', 'qraft-login-entry.spec.ts'],
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 600_000,
  reporter: [['list']],
  use: {
    video: 'off',
    screenshot: 'off',
    trace: 'off',
  },
});
