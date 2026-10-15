---
name: dual-instance-localstorage-degradation
description: 双开时第二个 Electron 实例的 Chromium 存储退化成内存——localStorage 读不到也写不进；长期状态别只放 localStorage
type: project
---


**现象（#1073 实测）**：同一 userData 跑两个实例时，**第二个实例的 localStorage 是完全空的、写进去也只留在内存**：它读不到第一个实例写入的值，自己写的值关掉即丢。第一个实例（持有 profile 锁的那个）一切正常。

实测探针（Playwright electron，A/B/C 三实例，修复前后都跑过）：

```
[probe] A 写入后读回: 2.0
[probe] B 读到 A 的同意记录: null      ← 第二实例读不到
[probe] B 是否展示确认门: 1            ← 后果：每次都弹
[probe] B 写入后读回: 2.0-from-B      ← 只在内存
[probe] 重开后 C 读到: 2.0  门: 0      ← B 的写入被丢弃
```

**Why:** 用户反馈「每次打开都强制看协议，没有记忆」——他们习惯在已有实例运行时再点一次图标（启动慢时尤其容易），于是每次都在「第二实例」里操作，同意永远不落盘。

**How to apply:**

- **别把需要跨启动持久的状态只放渲染层 localStorage**（同意状态、登录态、配置缓存…）。这个仓库的既有正确做法是主进程 userData 文件（`qraft-auth.json`、`qraft-billing-history.json`、`privacy-consent.json`）；localStorage 只当快速缓存。同步读的招：preload 里 `ipcRenderer.sendSync` 取一次（见 preload `privacy.initialConsentVersion`），渲染层判定保持同步、不闪现。
- **打包版已有单实例锁**（`src/main/index.ts`，`app.isPackaged` 分支，第二次启动聚焦已有窗口）；dev/E2E **故意不加锁**——E2E 并行 worker 依赖同 checkout 多开，且 dev userData 已按 checkout 路径 hash 隔离。
- 排查这类问题的入口：先看是不是「第二个实例」在操作（双开），再看该状态存哪。dev 下 userData 在 `%APPDATA%/miqi-desktop-dev/ws-<hash>`；打包版在 `%APPDATA%/miqi-desktop`。相关：[legal-documents-feature-map](legal-documents-feature-map.md)、[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)、[billing-history-missing-diagnosis](billing-history-missing-diagnosis.md)
