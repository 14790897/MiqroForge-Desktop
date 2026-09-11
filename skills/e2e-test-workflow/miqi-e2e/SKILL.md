---
name: miqi-e2e
description: |
  MiQi Desktop Electron E2E test patterns — platform-dependent features (WSL),
  CI workflow integration, setup-wizard navigation, resilient assertions across
  Linux/Windows CI.
  Triggers: "miqi e2e", "WSL e2e test", "MiQi CI", "playwright miqi"
agent_created: true
---

# MiQi Desktop E2E Patterns

Extends the general `e2e-test-workflow` skill with MiQi-specific patterns.

## Platform-dependent testing

### Resilient assertions across platforms

Tests must pass on both Linux and Windows CI. Use conditional logic based on
bridge API results rather than hard-coded platform expectations:

```ts
const check = await page.evaluate(() => (window as any).miqi.wsl.check());
// Linux CI: { isWindows: false, featureState: "not-supported" }
// Windows+WSL: { isWindows: true, featureState: "ready" | "installed-but-not-initialized" | ... }
const distros = (check as any)?.distros ?? [];
if (distros.length === 0) {
  // non-Windows or no-WSL state — verify informative empty state
} else {
  // WSL installed — verify monitoring UI
}
```

### Progress events may not fire on platform short-circuit

Platform-short-circuit handlers return immediately without firing IPC progress events.
On non-Windows, `wsl:installAndProvision` returns `{ success: false, error: "Not on Windows" }`
without any `safeSend` call. Tests must accept 0 events:

```ts
const events = await page.evaluate(async () => {
  const collected: any[] = [];
  const unsub = (window as any).miqi.wsl.onInstallProgress((data: any) => {
    collected.push({ ...data });
  });
  await (window as any).miqi.wsl.installAndProvision();
  await new Promise((r) => setTimeout(r, 500));
  unsub();
  return collected;
});

if (events.length === 0) {
  console.log('[test] ✅ 0 events (expected on non-Windows or WSL-ready)');
}
```

## Setup Wizard & Overlay Testing

### Full-screen overlays have no `<main>`

Setup Wizard renders as a full-screen overlay. `page.locator('main')` times out.
Use `page.locator('body')` instead:

```ts
// WRONG — times out
await page.locator('main').textContent();

// RIGHT
await page.locator('body').textContent({ timeout: 10_000 });
```

### Scroll for visibility in Settings tabs (TWO steps)

Buttons at the bottom of scrollable tabs need: (1) click correct tab, (2) scroll.
Previous tests may have left Settings on a different tab (e.g. WSL tab):

```ts
await page.locator('[data-testid="nav-system-settings"]').click();
await page.waitForTimeout(1500);

// 1. Click correct tab
const tab = page.getByRole('tab', { name: '通用' });
if (await tab.isVisible({ timeout: 3000 }).catch(() => false)) {
  await tab.click();
  await page.waitForTimeout(500);
}

// 2. Scroll to reveal
await page.getByText('重新配置').scrollIntoViewIfNeeded().catch(() => {});
await page.waitForTimeout(300);

const btn = page.getByRole('button', { name: '重新运行配置向导' });
```

### Tab navigation

```ts
await page.locator('[data-testid="nav-system-settings"]').click();
await page.waitForTimeout(1500);
await page.locator('[role="tab"]').filter({ hasText: 'WSL' }).click();
await expect(page.getByText('WSL 状态监控').first()).toBeVisible();
```

## CI Workflow Integration

### `if: always()` after flaky predecessors

```yaml
- name: Run sandbox E2E tests
  run: npx playwright test ... sandbox-*.spec.ts
  timeout-minutes: 20

- name: Run new E2E tests
  if: always()
  run: npx playwright test ... new-*.spec.ts
  timeout-minutes: 10
```

### Gate on build success (step id + outcome)

`if: always()` alone is too broad — runs even when `npm ci` was skipped.
Add `id` to build step, gate on `steps.build.outcome`:

```yaml
- name: Build renderer
  id: build
  run: npm run build

- name: Run new E2E tests
  if: always() && steps.build.outcome == 'success'
  run: npx playwright test ... new-*.spec.ts
  timeout-minutes: 10
```

### `pull_request` paths may block re-runs

Add `workflow_dispatch` for manual re-trigger:

```yaml
on:
  pull_request:
    paths: [...]
  workflow_dispatch:
```

## Bridge API Testing Without LLM

```ts
// Verify method exists
const hasMethod = await page.evaluate(() =>
  typeof (window as any).miqi?.wsl?.installAndProvision === 'function'
);
expect(hasMethod).toBe(true);

// Call and verify result shape
const result = await page.evaluate(() =>
  (window as any).miqi.wsl.installAndProvision()
);
expect(result).toHaveProperty('success');
expect(result).toHaveProperty('phase');
```

### Validating featureState enum

```ts
const validStates = [
  'not-supported', 'not-enabled', 'not-installed',
  'installed-but-not-initialized', 'ready',
];
expect(validStates).toContain(r.featureState);
expect(typeof r.rebootRequired).toBe('boolean');
```

## CI Polling with execute_code

Windows bash quoting is fragile for complex `gh` queries. Use Python `execute_code`:

```python
import subprocess, json, time

# Check step status
r = subprocess.run(
    ["gh", "run", "view", RUN_ID, "--repo", REPO,
     "--json", "jobs", "-q",
     '.jobs[] | select(.name=="JOB") | .steps[] | "\\(.name) -> \\(.status) \\(.conclusion)"'],
    capture_output=True, text=True, timeout=10,
    cwd=r"C:\git-program\test\MiQi-Desktop"
)
print(r.stdout.strip())

# Poll until complete
for i in range(30):
    time.sleep(20)
    # ... re-check
```

## 测试驱动开发（先测试，后代码）

**每次修改功能/修复 bug，必须遵循 TDD 顺序：先完善足够好的测试，再根据测试优化现有代码逻辑。** 不要反过来（先改代码、测试后补）。

流程：

1. **先写/完善一个能准确复现问题（或验证目标行为）的测试**，断言用户真正关心的结果。
   - 例：用户报告"手动选目录后 AI 仍报 home"，就写测试断言 `AI cwd reply 包含所选目录`（不是只断言 metadata/pill）。
   - 测试必须**失败**（或暴露 bug），证明它真的覆盖了问题。

2. **本地跑测试确认它失败/暴露 bug**，记录失败时的实际输出。

3. **根据测试结果修现有代码**，让测试通过。用 marker 探针（写临时文件）确认数据在关键链路的真实值，定位根因，不靠猜。

4. **本地跑测试确认通过**，再跑相关单测（pytest/vitest）防回归。

5. **截图给用户看**（见下一节），证明修复在 UI 层生效。

关键原则：
- 测试断言**用户可感知的结果**（AI 回复、UI 状态），而非内部实现细节。
- 临时验证用的 spec / marker 探针，验证后**删除**，不进 PR。
- 一个"足够好的测试"胜过十个模糊断言——它必须能区分"修好了"和"没修好"。

## 测试完成必须截图展示

每次 E2E 测试运行完毕（无论通过或失败），**必须**截图给用户看：

1. 测试结束后，找到 Playwright 保存的截图：
   ```bash
   find test-results -name "test-finished-*.png" -newermt "-5 minutes"
   ```
   失败时是 `test-failed-*.png` / `test-failed-1.png`。

2. 若未自动截图，在测试末尾手动补：
   ```ts
   await page.screenshot({
     path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
     fullPage: true,
   });
   ```

3. 用 `SendUserFile` 发送截图（**绝对 Windows 路径**，`display: 'render'`，`status: 'proactive'`，`caption` 说明验证了什么）。

4. 先用 luma-mcp 的 `image_understand`（OCR）自检截图内容符合预期，再发送——避免误发失败/旧截图。
