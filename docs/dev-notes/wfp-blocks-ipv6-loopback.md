---
name: wfp-blocks-ipv6-loopback
description: 本机网络过滤组件拦截回环（IPv6 ::1 + 按二进制身份拦未签名进程的 IPv4 回环），dev 连不上 Vite、打包版卡「启动中」，已有 #895 + #898 双层修复
type: project
---

本机环境的网络过滤组件（WFP 过滤器）拦截回环，有两层表现：

1. **IPv6 回环被拦**：对监听中的 `[::1]` socket connect 报 EACCES（或黑洞超时）。Node 25 把 `localhost` 解析为 `::1`，Vite 只绑 `[::1]:5173` → Electron `ERR_CONNECTION_TIMED_OUT (-118)`。已落地 PR #895（`server: { host: '127.0.0.1' }`）。
2. **按二进制签名身份拦未签名进程的 IPv4 回环 SYN**（签名/知名程序放行）：PyInstaller 打包的 exe、uv 托管的 Python 的 127.0.0.1 回环被静默丢弃，LAN/外网不受影响。Windows 无原生 socketpair（127.0.0.1 TCP + accept 模拟），asyncio 建事件循环时 `accept()` 永久阻塞 → bridge ready 发不出 → 打包版永久卡「启动中」。已落地并合并 PR #898（2026-09-01，develop）：`miqi/bridge/loopback_compat.py` 启动时 2 秒超时自检，不健康自动降级为 LAN socketpair（bind 具体 LAN 地址 + connect 局域网 IP，非广播路由候选 192.168.255.254/10.255.255.254/8.8.8.8），`server.py` 在事件循环创建前安装。打包机上 msvcp140 < 14.40 还会让 onnxruntime 导入失败，用 `scripts/check-build-env.py` 排查。

**Why:** 公开场合（PR、代码注释、对话、文档）不出现工具/软件名及敏感词，一律用技术化描述（WFP 过滤器拦截回环）。

**How to apply:** Electron dev 起不来报 localhost:5173 超时 → 查 #895 的 127.0.0.1 绑定是否还在；打包版卡「启动中」→ 看 bridge stderr 是否根本没出 ready，补丁自愈兜底；重新打包报 onnxruntime DLL 失败 → 先跑 `python scripts/check-build-env.py`。同环境见 [git-push-proxy-issue](git-push-proxy-issue.md)。
