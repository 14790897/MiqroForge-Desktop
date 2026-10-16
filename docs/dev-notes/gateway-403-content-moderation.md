---
name: gateway-403-content-moderation
description: MiQroForge 平台 AI 网关内容安全拦截返回 403 sensitive_word_detected；表现为「模型服务认证失败」误报
type: reference
---


平台 AI 网关对命中敏感词的内容返回：

```
403 {'error': {'code': 'sensitive_word_detected', 'message': '我们换一个话题吧', 'type': 'security_violation'}}
```

**为什么记**：这与认证失败表现为同一类错误（401/403 同族），极易误诊为凭据问题。
2026-09-10 实测：用户报告「登录后还是说要配置模型 / 认证失败」，桥日志显示实际是
403 内容安全拦截——凭据完全正常，直接探测网关用同一密钥返回 200。

**触发规律（2026-09-10 二分实测，确定性复现）**：
- 主信号是**手机号形态的数字串**：去掉分隔符后「11 位、以 1 开头」即被拦。
  `19216840100` / `13800138000` / `192.168.40.100` 全部 BLOCKED；
  12 位（`192.168.40.1000`、`192.168.040.100`）与 ≤10 位（`192.168.40.99`）放行
- 因此内网 IP `192.168.40.100`~`192.168.42.101` 这类**去点后恰为 11 位**的地址会被
  误判成手机号 —— 这正是用户 slurm skill 被拦的原因（`ssh hpcadmin@192.168.40.101`）
- 密码、用户名、`ssh` 本身都不触发；`192.168.1.100`/`10.0.0.101`/`172.16.40.101`
  （去点后 10 位）也不触发
- 判定带上下文敏感性：同一个 IP 在表格行 / 中文描述句里可能放行
  （`服务器 192.168.40.102` ok，`服务器192.168.40.102` 与 `IP: 192.168.40.102` 拦）

**如何应用**：
- 遇到「模型服务认证失败」「去选择模型」类报错，先看桥日志（`<workspace>/logs/bridge-*.log`）
  确认 error code，再判断是凭据问题（401）还是内容拦截（403 sensitive_word_detected）
- 快速区分：用 token.json 的 `aiGateway.encryptedApiKey` 直接 POST 网关
  `POST {origin}{GATEWAY_PREFIX}/v1/messages`（header `X-Api-Key`）——返回 200 说明凭据没问题
- 定位触发内容：对候选文本逐行/二分发送即可复现（确定性）
- skill 文档写内网地址时用**主机名别名**（`ssh ctl`，配 ssh config）而不是裸 IP——
  仓库内置 `miqi/skills/slurm/SKILL.md` 就是这么写的（无 IP 无密码），
  用户 workspace 的手写副本才是踩雷的那份
- 分类修复见 PR #1027（`ErrorKind.CONTENT_BLOCKED`）；未修复的旧版会把两者
  混为 AUTH，用户看到的「认证失败」文案是误报

相关：[ai-gateway-https-policy](ai-gateway-https-policy.md)、[qraft-test-account-credentials](qraft-test-account-credentials.md)
