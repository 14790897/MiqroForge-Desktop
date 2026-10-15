---
name: e2e-direct-screen-recording
description: E2E 录屏用 Electron desktopCapturer+MediaRecorder 录窗口流（真·窗口录制，全自动）；备选 ffmpeg gdigrab 桌面区域裁剪；不要截图拼帧/抽帧
type: feedback
---


E2E 要给用户/PR 的「录屏」必须是**连续视频**、且是**真·窗口录制**，**不要截图拼帧**（`page.screenshot` 逐帧合成），也不许**抽帧**（拼接不连续两段 → 跳变）。

**Why:** 2026-09-11 用户多次纠正——① 帧间隔 1.5s → 幻灯片；② 抽帧拼接 → 中间跳变「完全不对」；③ 我写的「录窗口」实际是桌面区域裁剪，用户指出「你没有录制窗口」。

**首选做法：Electron 内 `desktopCapturer` + `getDisplayMedia` + `MediaRecorder`（真·窗口录制、窗口跟随、全自动、无需 OBS）**，Electron 39 实测通过：

1. 主进程（测试里 `electronApp.evaluate`）设 displayMedia 处理器，选应用窗口：
   ```js
   await electronApp.evaluate(async ({ session, desktopCapturer }) => {
     session.defaultSession.setDisplayMediaRequestHandler(async (_req, cb) => {
       const srcs = await desktopCapturer.getSources({ types: ['window'] });
       const win = srcs.find(s => s.name === 'MiQroForge Desktop') ?? srcs[0];
       cb({ video: win });
     });
   });
   ```
2. 渲染进程（`page.evaluate`）：`navigator.mediaDevices.getDisplayMedia({video:true,audio:false})` → `new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp9',videoBitsPerSecond:2_500_000})` → `rec.ondataavailable` 收 chunk → `rec.start(500)`；把 `{rec,chunks,stream}` 挂在 `window.__rec`。
   - 验证绑对窗口：`stream.getVideoTracks()[0].getSettings().displaySurface === 'window'`（实测 1264x820）。
3. 停止：`rec.stop()` → 等 `onstop` → `new Blob(chunks)` → `arrayBuffer()` → base64 → **`page.evaluate` 返回给测试写盘**（110s 约 1.4MB mp4 / webm 3.7MB，CDP 传 base64 可接受）。
4. 转 mp4：`ffmpeg -i x.webm -vf scale=trunc(iw/2)*2:trunc(ih/2)*2 -c:v libx264 -crf 26 -pix_fmt yuv420p -movflags +faststart x.mp4`。
5. 裁剪只允许**连续** `-ss/-t`（不跳帧）；完成后静止段可去掉。

**备选（区域裁剪，非窗口）——ffmpeg gdigrab**：
```bash
ffmpeg -y -f gdigrab -framerate 15 -offset_x X -offset_y Y -video_size WxH -i desktop -t <秒> \
  -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -pix_fmt yuv420p -c:v libx264 -preset veryfast out.mp4
```
坐标 = `BrowserWindow.getBounds()` × `scaleFactor`。坑：① `-i title="<标题>"` 对 GPU 合成 Electron 窗口**全黑**（54s 仅 62KB）；② 加 `--disable-gpu` 让窗口可被 GDI 抓 → **应用启动即崩溃**（停 splash）；③ 它是**固定坐标区域**，窗口移动/被遮挡会录偏。

**其他**：Playwright 的 Electron 启动**不支持 `recordVideo`**（只有 browser context 有）；config 里 electron project 的 `use.video:'on'` 对 Electron 无效。**OBS 窗口采集**需在 UI 里选窗口（`window` 标识格式不透明，写配置猜不中；同标题多窗口易选错）——故未采用。

**校验非黑屏**：抽一帧后用 **Read 直接读该 png**（见 [use-luma-mcp-for-images](use-luma-mcp-for-images.md)），或按大小粗判（黑屏 54s ~60KB）。

**无 LLM 回合的 UI 演示（如反馈提交）**：没有「停止生成」按钮可判停——用固定节奏：`pressSequentially(..., {delay: 50-60})` 逐字输入 + 每步 `waitForTimeout(400-800)` 停一下，收尾多停 1-2s 再 stop。多场景演示可以**分场景各录一段连续视频再 concat**（`[0:v][1:v]concat=n=2:v=1:a=0`，各段内部连续不算拼帧），但**绝不在一段内跳时间**。PR 贴法：release 资产直链 + `<video src=... controls>` 标签（GitHub PR 正文可能不过滤，直链一定可用，两者都给）。

**⚠️ 2026-09-15 实测：`<video>` 标签播不了 release 资产**——`curl -I` 看 `_gh-imgup` 的 mp4 直链：GitHub 302 到 release-assets.githubusercontent.com，响应头 `Content-Type: application/octet-stream` + `X-Content-Type-Options: nosniff`，浏览器拒绝当视频/图片内联渲染（同机制下 png 截图能显示是因为 `<img>` 走了 GitHub 的 camo 代理）。**结论：视频只给可点击的下载直链 + 说明「点击下载播放」（或找一个真正支持内嵌播放的宿主）**，别指望 `<video>` 标签。

**⚠️ 录屏产物也会被 Playwright 清空**：`test-results/` 是默认 outputDir，跑任一 project 都会清空（2026-09-15 又踩一次：录完功能演示 mp4 → 接着跑第二段录屏 spec → 第一段被删，只能重录）。顺序必须是「生成 → 立刻 `cp` 出 test-results → 再跑别的」。

**何时停止录制（判停）**：用 `page.getByLabel('停止生成')`（composer 的停止按钮，aria-label）——**出现 = 回合在跑，消失 = 回合结束**。实测 26 秒的回合能准确停表。注意必须要求**持续消失**（连续 ≥3 次轮询 / ~2s）才算结束：工具调用之间、等待确认卡时该按钮会**短暂消失**，一见到就停会把录屏截断在提交前（实测被截成 26s→对，但也有过误判风险）。判停比「固定时长 + 事后裁剪」可靠。

**附带坑（确认卡）**：`ask_user_confirm_card` 的卡片形状不定——单个 `confirm-card-primary`；也可能**多个元素都带 `confirm-card-primary`**（双选项卡片两个实心按钮，如「提交到 all 分区」/「提交到 vip_192_al 分区」），或走 `confirm-card-choice`。Playwright 下多元素匹配会让 `locator.isVisible()` 失败（strict mode）→ 点击逻辑静默不生效 → 卡片超时、作业不提交。**一律用 `.first()`**，并优先判断 `confirm-card-area` 是否在屏；primary 不在再点第一个非「取消」的 choice、用 `click({force:true})`。

相关：[auto-create-pr-after-task](auto-create-pr-after-task.md)。
