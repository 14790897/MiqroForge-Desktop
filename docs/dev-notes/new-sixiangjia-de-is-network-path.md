---
name: new-sixiangjia-de-is-network-path
description: new.sixiangjia.de 慢是本地出国线路问题，不是服务端；服务端 TLS/h2/h3 已配好，别去调它
type: reference
---


`new.sixiangjia.de` = Oracle Cloud 新加坡（158.178.246.70），仅被 CI 的 PR-Agent 当 `OPENAI.API_BASE` 用（`.github/workflows/pr-agent.yml`），桌面端运行时不用它。

**2026-10-09 实测结论：慢在网络路径，不在服务端。**
- 服务端已配好，无需优化：默认 TLS 1.3、ALPN 协商出 h2、`alt-svc: h3=":443"` 已广告 HTTP/3、TLS 会话复用正常。之前 curl 报的「TLS 1.2 要 4s」是 Windows schannel 版 curl 的假象，openssl 实测默认就是 1.3。
- 瓶颈：用户本机（中国电信 202.97 出口）到新加坡 RTT ≈ 300ms，且该链路丢包（TCP 建连耗时呈 0.22→1.38→3.25→7.32s 指数退避，偶发 21s 失败/RST）。
- 佐证：用户换别的网络就很快 → 同一服务器，不同路径，快慢差异全在接入网。
- 对照：`skills.sixiangjia.de` 是 Vercel（美国 anycast），国内访问同样慢；`www.baidu.com` 基线 0.38s。

**How to apply:** 再遇到「这个域名/服务慢」，先分段实测（dns/tcp/tls/ttfb）再下结论；**务必先 ping 本机网关**——该机 ping 自己第一跳网关 avg 77ms / max 233ms（正常应 1–3ms），说明客户端本地链路本身就在抖，此时国内外一起慢，不能全赖出国线路。服务端 TLS 层别乱提优化建议；给用户的建议应落在换运营商/换线路/中转，而不是改代码或调服务器。相关：[platform-domain-migration](platform-domain-migration.md)、[git-push-proxy-issue](git-push-proxy-issue.md)。
