---
name: e2e-test-workflow
description: |
  Complete E2E testing workflow for Electron apps with Playwright, including:
  test spec creation, React textarea handling (type vs fill), approval dialog
  auto-click, *:* wildcard pre-approval, PPTX content verification, screen
  recording (Electron desktopCapturer window capture), session streaming isolation fix,
  and multi-agent review checklist.
  Triggers: "测e2e", "写E2E测试", "write e2e test", "run e2e", "e2e录屏",
  "approval handling", "PPTX verification", "write playwright test",
  "session isolation", "流式隔离".
agent_created: true
---

# E2E Test Workflow

## Quick reference

```bash
# Build + run single test
cd apps/desktop && npm run build && npx playwright test --config=playwright.config.ts --project=electron -g "test name"

# Run with list reporter
npx playwright test tests/e2e/file.spec.ts --config=playwright.config.ts --project=electron --reporter=list

# Clean workspace between runs
rm -rf ~/.forge/workspace/sessions
```

## 先本地跑，再上 CI（硬性规则）

**任何新增/修改的 E2E 测试，必须先在本机跑通，再推送/提 PR。** CI 上的
e2e 任务（electron-e2e / wsl-e2e / macos-e2e）只做回归兜底，不用作首次
验证或调试手段：

1. 本地跑法（见上方 Quick reference）：
   ```bash
   cd apps/desktop && npm run build && npx playwright test --config=playwright.config.ts --project=electron -g "test name"
   ```

2. 为什么必须先本地跑：
   - CI 的 e2e 队列慢（常 10 分钟以上），失败一轮就白等，改一轮又一轮
   - 调试产物只有本地能拿：test-results 截图、录屏（desktopCapturer 窗口录制）、
     Playwright trace；本技能"测试完成必须截图展示"也依赖本地
     test-results 产物
   - 本地失败立刻能看到页面实际状态，CI 上只能靠日志猜

3. worktree 里跑之前，先 junction 主仓库 node_modules（免 npm ci）：
   ```bash
   cmd //c "mklink /J <worktree>\apps\desktop\node_modules C:\git-program\test\MiQi-Desktop\apps\desktop\node_modules"
   ```
   ⚠️ 不要 junction `out/`（构建产物）：`src/main/index.ts` 用
   `join(__dirname, '../../..')` 推 repoRoot，`__dirname` 经 junction 会
   解析回**主仓库**路径，桥接会跑主仓库代码，worktree 改动对 E2E 完全
   不可见。E2E 前必须本地 `npm run build`（node_modules junction 可以
   保留，build 正常输出到 worktree 自己的 apps/desktop/out）。

4. 本地跑通 → 推分支提 PR → CI 的 e2e 通过才算完。若 CI 失败而本地通过，
   优先怀疑平台差异（Linux/Windows 断言、mock 可达性），参考 miqi-e2e
   技能的"Platform-dependent testing"小节。

## Test spec structure

Every E2E test spec follows this pattern:

```ts
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT, waitForInputReady, createNewConversation,
  launchElectronApp, closeElectronApp,
} from './helpers/electron-setup';

test.describe('Feature Name E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test('test name', { timeout: LLM_TIMEOUT }, async () => {
    // ... test body ...
  });
});
```

## React textarea: type() NOT fill()

`fill()` sets DOM value directly — React onChange never fires, Enter key
doesn't submit. Always use `type()` which triggers React event handlers:

```ts
async function sendAndWait(page: Page, text: string, loopTimeout = 180_000) {
  const inputX = page.locator('textarea, [contenteditable="true"]').last();
  await expect(inputX).toBeVisible({ timeout: 10000 });
  await inputX.click();
  await inputX.fill('');   // clear first
  await inputX.type(text); // triggers React onChange
  await inputX.press('Enter');
  await page.waitForTimeout(1500);
  await approveLoop(page, loopTimeout);
}
```

## Approval dialog handling

Two approaches, use BOTH for robustness:

### 1. *:* wildcard (global pre-approve)

Add to `miqi/execution/permission_engine.py` session + global permanent allowlist
check. Then in test, call BEFORE sendMessage:

```ts
await page.evaluate(() =>
  (window as any).miqi.approvals.addPermanent('*:*', 'always'),
);
```

### 2. Auto-click loop (per-dialog)

```ts
async function approveLoop(page: Page, timeout = 180_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const btn = page.getByRole('button', { name: '持久允许' })
      .or(page.getByRole('button', { name: '永久允许' }));
    if (await btn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await btn.click();
    }
    const thinking = await page.getByText('Thinking…').isVisible().catch(() => false);
    if (!thinking) break;
    await page.waitForTimeout(1000);
  }
}
```

## PPTX content verification

Python script using python-pptx. Auto-finds latest pptx recursively:

```python
"""verify-pptx.py"""
import json, sys, os, glob
from pathlib import Path
from pptx import Presentation

workspace = sys.argv[1]
files = glob.glob(os.path.join(workspace, "**", "*.pptx"), recursive=True)
filepath = max(files, key=os.path.getmtime)

prs = Presentation(filepath)
texts = [p.text.strip() for s in prs.slides
         for sh in s.shapes if sh.has_text_frame
         for p in sh.text_frame.paragraphs if p.text.strip()]
all_text = "\n".join(texts)

result = {"slides": len(prs.slides), "texts": texts, "pass": True, "checks": []}
def check(label, condition, detail=""):
    result["checks"].append({"label": label, "pass": bool(condition), "detail": detail})
    if not condition: result["pass"] = False

# Add checks here...
json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
sys.exit(0 if result["pass"] else 1)
```

In test, call with execSync + try/catch:

```ts
const { execSync } = require('node:child_process');
const PY = '"C:\\Users\\...\\python.exe"';
let result;
try {
  const out = execSync(`${PY} "${verifier}" "${ws}"`, { encoding: 'utf8', timeout: 15000 });
  result = JSON.parse(out);
} catch (e: any) {
  result = JSON.parse(e.stdout || '{"pass":false,"checks":[]}');
}
if (!result.pass) {
  const failed = result.checks.filter((c: any) => !c.pass).map((c: any) => c.label);
  throw new Error(`Checks failed: ${failed.join(', ')}`);
}
```

## Screen recording (Electron desktopCapturer — 真·窗口录制)

**不要用 `page.screenshot` 逐帧拼帧**（帧间隔大 → 幻灯片；拼接不连续两段 → 跳变），
也不要抽帧。用 Electron 自带 `desktopCapturer` + `getDisplayMedia` +
`MediaRecorder` 在应用内录**窗口流**：真·窗口、窗口跟随、全自动，播放器能正常播。

现成参考实现（`startRecording` / `drainChunks` / `stopRecording` 可直接照抄）：
[apps/desktop/tests/e2e/record-bvse-skill.spec.ts](../../apps/desktop/tests/e2e/record-bvse-skill.spec.ts)

```ts
// 1) 主进程：按「当前窗口标题」精确挑捕获源
const title = await electronApp.evaluate(async ({ session, desktopCapturer, BrowserWindow }) => {
  const t = BrowserWindow.getAllWindows()[0]?.getTitle() ?? '';
  session.defaultSession.setDisplayMediaRequestHandler(async (_req, cb) => {
    const srcs = await desktopCapturer.getSources({ types: ['window'] });
    const win = srcs.find(s => s.name === t) ?? srcs.find(s => /miqroforge/i.test(s.name)) ?? null;
    // 不要 `?? srcs[0]`：匹配失败会静默录到别的程序窗口
    if (!win) { console.log('[record] 可用窗口：' + srcs.map(s => s.name).join(' | ')); return cb({}); }
    cb({ video: win });
  });
  return t;
});

// 2) 渲染进程：开录（1s 分片，测试侧每 ~10s drain 落盘，长录像不撑爆内存）
const info = await page.evaluate(async () => {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  const rec = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9', videoBitsPerSecond: 1_200_000 });
  (window as any).__rec = { rec, chunks: [] as Blob[] };
  rec.ondataavailable = (e: BlobEvent) => { if (e.data.size) (window as any).__rec.chunks.push(e.data); };
  rec.start(1000);
  const t = stream.getVideoTracks()[0];
  return { surface: t.getSettings().displaySurface, w: t.getSettings().width, h: t.getSettings().height, label: t.label };
});
expect(info.surface).toBe('window');           // 录错层（屏幕而非窗口）会被抓到
expect(info.label).toMatch(/miqroforge/i);     // 回退到别家窗口也会被抓到
```

停止：`rec.stop()` → 等 `onstop` → 把 `chunks`（`splice(0)` 取走）逐个 base64 交回测试
`appendFileSync` 写盘（110s ≈ 1.4MB mp4 / 3.7MB webm，CDP 传 base64 可接受）。

**判停**：`page.getByLabel('停止生成')` 出现 = 回合在跑、**持续消失**（≥3 次轮询 ~2s）
= 回合结束。工具调用之间/等待确认卡时该按钮会短暂消失，一见就停会把录屏截断在提交前。

```bash
# webm → mp4（libx264 要求偶数宽高）
ffmpeg -y -i rec.webm -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -c:v libx264 -crf 26 -pix_fmt yuv420p -movflags +faststart rec.mp4
```

备选（区域裁剪，非窗口）：`ffmpeg -f gdigrab -offset_x X -offset_y Y -video_size WxH -i desktop`；
坑：`-i title="<标题>"` 对 GPU 合成的 Electron 窗口全黑，加 `--disable-gpu` 该应用启动即崩溃，
且固定坐标会因窗口移动/被遮挡录偏。Playwright 的 Electron 启动**不支持 `recordVideo`**
（`use.video:'on'` 对 Electron 无效）。

录制已知坑：

- **窗口必须真在屏**：E2E 默认把窗口停在屏幕外（`MIQI_E2E_OFFSCREEN`，见
  [helpers/electron-setup.ts](../../apps/desktop/tests/e2e/helpers/electron-setup.ts)）→
  用 `launchElectronApp(..., { showWindow: true })`，否则抓到空白/黑屏。真·最小化同样录成黑屏。
- **`test-results/` 会被清空**：它是默认 outputDir，跑任一 project 都清 →
  录完立刻 `cp` 出去再跑别的 spec，否则上一段录像被删。产物建议放 `$RECORD_OUT_DIR` 这类独立目录。
- **无 LLM 回合的演示**（如反馈提交）没有「停止生成」可判停：用固定节奏
  `pressSequentially(..., { delay: 50-60 })` 逐字输入 + 每步 `waitForTimeout(400-800)`。
  多场景可以各录一段**连续**视频再 `concat`（各段内部连续不算拼帧），绝不在一段内跳时间。
- **贴 PR**：`<video>` 标签播不了 release 资产（302 后 `Content-Type:
  application/octet-stream` + `X-Content-Type-Options: nosniff`）→ 给可点击的下载直链并注明
  「点击下载播放」，别指望内嵌播放。
- **校验非黑屏**：抽一帧用 Read 直接读该 png，或按体积粗判（黑屏 54s ≈ 60KB）。

## Test selector tips

- Prefer text selectors: `getByText('Tasks')`, `getByPlaceholder('...')`
- Scope to `<main>`: `page.locator('main').getByText(...)` avoids sidebar false positives
- Short prompts for AI: `只回答Y` not verbose requests
- `SKIP_SANDBOX_ON_CI = !!process.env.CI` for bwrap-dependent tests

## Session Streaming Isolation Fix

Three-layer pattern for cross-session IPC event leaks.

### Layer 1: Backend — Tag events with session identity

```python
session_key = params.get("session_key")
if isinstance(data, dict):
    data["session_key"] = session_key
```

### Layer 2: IPC types — Add session_key field

Add `session_key?: string` to all streaming event interfaces.

### Layer 3: Frontend — Guard handlers

```ts
handler.onEvent((data) => {
  if (data.session_key && data.session_key !== currentSessionRef.current) return;
  // ... process event
});
```

Plus useEffect cleanup — tear down OLD listeners BEFORE updating the ref.
This makes the per-handler session_key guard a defence-in-depth measure rather
than the sole mechanism:

```ts
useEffect(() => {
  cleanupListeners();  // tear down in-flight stream listeners first
  currentSessionRef.current = sessionKey;
  // ... reset state, load history, register new listeners
}, [sessionKey]);
```

**Key insight**: With `<ChatConsole key={sessionKey}>`, React unmounts the old
component and mounts a new one on session switch.  The old listeners should be
torn down in the new component's effect, not in the old component's cleanup
return — otherwise async operations in the effect could still reference stale
listener closures.

### E2E Streaming isolation test

```ts
async function sendWithoutWaiting(page, text) {
  const inputX = page.locator('textarea').last();
  await inputX.fill(text);
  await inputX.press('Enter');
}
await sendWithoutWaiting(page, markerA);
// Wait for "Thinking…" to confirm stream has ACTUALLY started before
// switching sessions.  This is deterministic regardless of CI speed.
await expect(page.getByText('Thinking…')).toBeVisible({ timeout: 15_000 });
await createNewConversation(page);
await sendAndWait(page, markerB);
expect(await page.textContent()).not.toContain(markerA);
```

## CI Polling

```bash
# One-shot all PRs
for pr in PR_LIST; do
  echo "=== PR $pr ==="
  gh pr checks $pr --repo OWNER/REPO | grep -E "pass|fail|pend"
done

# Continuous polling
while true; do
  clear; echo "=== $(date +%H:%M:%S) ==="
  for pr in PR_LIST; do
    gh pr checks $pr --repo OWNER/REPO | grep -cE "fail" | xargs echo "fail="
  done
  sleep 60
done

# Rerun failed job
gh run rerun <run-id> --repo OWNER/REPO --failed

# Debug CI
gh run view <run-id> --repo OWNER/REPO --log --job=<job-id> | grep "Error:"
```

## Git Rebase Survival

- `git checkout --theirs` can silently drop non-conflicting changes. Verify: `git diff origin/target.. -- file`
- Mock dict-shape tests: `@pytest.mark.skip` instead of deleting
- Rebase duplicate-commit detection: skip commits already squashed into base branch

## 协作审查清单 (Multi-Agent Review Checklist)

从 PR #214 实战总结的审查要点，每次 review 流式消息/会话相关 PR 时逐项检查：

### Backend (Python)

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 事件注入是否覆盖所有路径 | `_emit` 和 `_emit_terminal` 是否为仅有的两个发送出口 |
| 2 | `session_key` 默认值 | 空字符串 `""` 作为默认值，前端 guard 用 truthiness 检查兼容旧后端 |
| 3 | dict 可变性 | `data["session_key"] = session_key` 直接修改 dict，确认所有调用方传的是新 dict 而非共享对象 |
| 4 | SQLite WAL 一致性 | `sqlite_store.py` / `history_runtime.py` / `thread_store.py` / `stored_runtime.py` 四者 WAL 模式是否一致 |
| 5 | 测试断言值 | `@pytest.mark.skip` 的测试如果更新了断言，要确认传入参数与期望值匹配（别把 `session_id` 填进 `session_key`） |

### Frontend (TypeScript/React)

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 监听器生命周期 | `<ChatConsole key={sessionKey}>` 依赖 React 卸载/挂载来清理，确认 cleanup 在 effect 开头而非 return |
| 2 | 类型一致性 | 四个 IPC handler 的参数类型应一致——`onProgress` 不能用 `any` |
| 3 | `session_key` 可选的原因 | 注释应说明 "向后兼容"，不是忘了写 |
| 4 | `currentSessionRef` 是 ref 而非 state | 闭包里读取的是最新值，不会 stale |

### E2E 测试

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 禁止 `waitForTimeout` 当同步点 | 用 `expect.toBeVisible('Thinking…')` 确认流式已开始，用 `expect.not.toBeVisible('Thinking…')` 确认已完成 |
| 2 | `page.evaluate` 错误日志 | `catch { /* skip */ }` → `catch (e) { results.push({..., error: String(e)}) }` |
| 3 | 会话隔离验证 | 必须同时验证"B 不含 A 的内容"和"B 含自己的内容" |
| 4 | 长任务验证 | 用多文件代码生成（如 Flask 博客系统）确保流式跨度足够长 |

## 测试驱动开发（先测试，后代码）

**每次修改功能/修复 bug，必须遵循 TDD 顺序：先完善足够好的测试，再根据测试优化现有代码逻辑。** 不要反过来（先改代码、测试后补）。

流程：

1. **先写/完善一个能准确复现问题（或验证目标行为）的测试**，断言用户真正关心的结果。
   - 例：用户报告"手动选目录后 AI 仍报 home"，就写测试断言 `AI cwd reply 包含所选目录`（不是只断言 metadata/pill）。
   - 测试必须**失败**（或暴露 bug），证明它真的覆盖了问题。

2. **本地跑测试确认它失败/暴露 bug**，记录失败时的实际输出（如 `AI cwd reply: /home/miqi/workspace`）。

3. **根据测试结果修现有代码**，让测试通过。用 marker 探针（写临时文件）确认数据在关键链路的真实值，定位根因，不靠猜。

4. **本地跑测试确认通过**，再跑相关单测（pytest/vitest）防回归。

5. **截图给用户看**（见下一节），证明修复在 UI 层生效。

关键原则：
- 测试断言**用户可感知的结果**（AI 回复、UI 状态），而非内部实现细节。
- 临时验证用的 spec / marker 探针，验证后**删除**，不进 PR。
- 一个"足够好的测试"胜过十个模糊断言——它必须能区分"修好了"和"没修好"。

## 测试完成必须截图展示

每次 E2E 测试运行完毕（无论通过或失败），**必须**截图给用户看：

1. 测试结束后，找到 Playwright 保存的截图
   ```bash
   find test-results -name "test-finished-*.png" -newermt "-5 minutes"
   ```
   失败时则是 `test-failed-*.png` / `test-failed-1.png`。

2. 若测试未自动截图（`screenshot` 配置为 off），在测试末尾手动补一张：
   ```ts
   await page.screenshot({
     path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
     fullPage: true,
   });
   ```

3. 用 `SendUserFile` 把截图发送给用户查看：
   - 用**绝对 Windows 路径**（`C:\Users\...`），不要用 `/tmp/...`（Windows 下 `/tmp` 映射不稳定）
   - `display: 'render'` 让用户在侧栏直接看到
   - `status: 'proactive'` 主动推送
   - `caption` 说明这张截图验证了什么（如"AI 报告工作目录为 C:\git-program\auto_display_light"）

4. 用 luma-mcp 的 `image_understand`（OCR 模式）先自检截图内容是否符合预期，再发送——避免把失败/旧截图误发给用户。

