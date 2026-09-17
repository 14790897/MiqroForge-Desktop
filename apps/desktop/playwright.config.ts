import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/smoke',
  testMatch: '**/*.spec.ts',
  testIgnore: '**/*.test.ts', // Exclude vitest files from Playwright scan
  fullyParallel: true,
  retries: 0,
  workers: 4,
  reporter: [
    ['html', { outputFolder: 'test-reports/html', open: 'never' }],
    ['json', { outputFile: 'test-reports/results.json' }],
  ],
  timeout: 30000,
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  // ---- Projects -----------------------------------------------------------
  projects: [
    // ① Smoke tests: mock bridge → runs in Chromium browser
    {
      name: 'smoke',
      testMatch: ['smoke.spec.ts', 'issue-*.spec.ts', 'logs.spec.ts'],
      use: {
        ...devices['Desktop Chrome'],
        baseURL: 'http://localhost:3458',
        headless: true, // CI runs headless; use --headed locally
      },
    },
    // ② Electron E2E: launches real desktop app via _electron.launch()
    //    Playwright 1.58+ (PR #39012) fixed the Electron 34 compatibility
    //    issue by switching from CLI flag to appendSwitch().
    {
      name: 'electron',
      testDir: './tests/e2e',
      testMatch: ['*.spec.ts'],
      timeout: 600_000, // 10 min — pptx-generator + LLM can be slow
      retries: process.env.CI ? 2 : 0, // CI: 3 attempts for transient WSL/LLM flakiness
      use: {
        video: 'on',
        screenshot: 'on',
      },
    },
  ],

  // ---- webServer (only needed by smoke project) ---------------------------
  // Electron project does NOT need a webServer — skip via env var.
  webServer:
    process.env.PLAYWRIGHT_SKIP_WEB_SERVER === '1'
      ? undefined
      : {
          command: 'python -m http.server 3458 --directory out/renderer',
          url: 'http://localhost:3458',
          reuseExistingServer: true,
          timeout: 180_000, // macOS CI can be slow to start
        },
});
