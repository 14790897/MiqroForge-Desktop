/**
 * MiQroForge #923 真实环境网关实测（可选，默认跳过）。
 *
 * 目标：走生产 OAuth 登录，从 /oauth2/userinfo 获取 encryptedApiKey（腾讯云消费者密钥），
 * 实测 AI 网关 http://118.25.115.164/deepseek/v1/messages（X-Api-Key）的可用性、
 * 响应是否 DeepSeek 兼容、SSE 流式输出、模型可用性；结论供 #922 网关接入参考。
 *
 * 用法（凭据只经环境变量注入，勿写死、勿入库；全部输出经 maskSecret 脱敏）：
 *   QRAFT_LIVE=1 QRAFT_PHONE=<账号> QRAFT_PASSWORD=<密码> \
 *     npx vitest run src/main/qraft/live.ai-gateway.test.ts
 *
 * 可选覆盖：
 *   QRAFT_ACCESS_TOKEN  给定则跳过登录，只跑 userinfo + 网关段（重试免重登）
 *   QRAFT_BASE_URL      平台 baseUrl，默认 https://forge.miqroera.com/api
 *   QRAFT_CLIENT_ID     默认 miqi
 *   QRAFT_CLIENT_SECRET 默认 miqi123456
 *   QRAFT_REDIRECT_URI  生产必须为平台注册值，默认 http://localhost:38000/callback
 *   QRAFT_GATEWAY_BASE  网关 base，默认 http://118.25.115.164
 *
 * 设计：userinfo 前提不满足（无 encryptedApiKey / aiGatewayStatus!=='active'）即判失败，
 * 属平台侧开通问题；其余各网关探测以 [gateway] 行打印 PASS/FAIL 记录，不因单点结果红整测，
 * 便于把真实响应形态固化后作为 #922 的依据。
 */

import { describe, expect, it } from 'vitest';
import { CookieJar } from './cookie-jar';
import { createQraftClient, type QraftLogger, type ResolvedQraftConfig } from './client';
import { maskSecret } from './rsa';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';
const ACCESS_TOKEN = process.env.QRAFT_ACCESS_TOKEN ?? '';
// 快路径只依赖 access_token，无需手机号/密码登录
const READY = LIVE && (ACCESS_TOKEN !== '' || (PHONE !== '' && PASSWORD !== ''));

const BASE_URL = process.env.QRAFT_BASE_URL ?? 'https://forge.miqroera.com/api';
const CLIENT_ID = process.env.QRAFT_CLIENT_ID ?? 'miqi';
const CLIENT_SECRET = process.env.QRAFT_CLIENT_SECRET ?? 'miqi123456';
const REDIRECT_URI = process.env.QRAFT_REDIRECT_URI ?? 'http://localhost:38000/callback';
const GATEWAY_BASE = process.env.QRAFT_GATEWAY_BASE ?? 'http://118.25.115.164';
// 平台确认：模型消息端点为 /miqroera-deepseek/v1/messages（不是 /deepseek/v1/messages）。
const GATEWAY_PREFIX = process.env.QRAFT_GATEWAY_PREFIX ?? '/miqroera-deepseek';
const GATEWAY_MESSAGES =
  process.env.QRAFT_GATEWAY_ENDPOINT ?? `${GATEWAY_BASE}${GATEWAY_PREFIX}/v1/messages`;

const CONFIG: ResolvedQraftConfig = {
  baseUrl: BASE_URL,
  clientId: CLIENT_ID,
  clientSecret: CLIENT_SECRET,
  redirectUri: REDIRECT_URI,
};

const silentLog = (() => undefined) as unknown as QraftLogger;

const REPORT: string[] = [];
function report(line: string): void {
  REPORT.push(line);
  console.log(`[gateway] ${line}`);
}

/** 标量值安全摘要：字符串只留头尾，其余给类型名，避免把密钥/敏感字段打全。 */
function safeScalar(key: string, value: unknown): string {
  if (value == null) return String(value);
  if (typeof value === 'string') {
    if (value === '') return '(empty)';
    // 只脱敏看起来像密钥/身份的值；短的非敏感字段直接展示便于核对
    if (value.length > 16) return maskSecret(value);
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return `[array:${value.length}]`;
  if (typeof value === 'object') return `[object]`;
  return String(value);
}

function truncate(s: string, n = 800): string {
  return s.length <= n ? s : `${s.slice(0, n)}…(截断 ${s.length}→${n})`;
}

interface RawResp {
  status: number;
  contentType: string | null;
  bodyText: string;
}

async function rawFetch(
  url: string,
  init: { method?: string; headers?: Record<string, string>; body?: string },
  timeoutMs: number
): Promise<RawResp> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: ctrl.signal });
    const bodyText = await res.text();
    return { status: res.status, contentType: res.headers.get('content-type'), bodyText };
  } catch (err) {
    const cause =
      err instanceof Error && err.cause instanceof Error ? `（cause=${err.cause.message}）` : '';
    throw new Error(
      `fetch ${init.method ?? 'GET'} ${url} 失败: ${err instanceof Error ? err.message : String(err)}${cause}`
    );
  } finally {
    clearTimeout(timer);
  }
}

describe.skipIf(!READY)('#923 真实环境实测：OAuth → userinfo encryptedApiKey → AI 网关', () => {
  it('完整链路：登录 → userinfo 取 encryptedApiKey → 网关非流式/SSE/https/模型', async () => {
    const client = createQraftClient({ log: silentLog });

    // ── ① 取 token：快路径 ACCESS_TOKEN，否则走生产 OAuth 完整登录 ──────
    let accessToken = ACCESS_TOKEN;
    let refreshToken: string | undefined;
    if (ACCESS_TOKEN !== '') {
      report('登录：跳过（使用 QRAFT_ACCESS_TOKEN）');
    } else {
      const jar = new CookieJar();
      const loginAccount = await client.platformLogin(CONFIG, PHONE, PASSWORD, jar);
      report(`登录 OK（${new URL(BASE_URL).host}）：nickname=${maskSecret(loginAccount.nickname)}`);
      const tokens = await client.authorizeFlow(CONFIG, jar);
      accessToken = tokens.accessToken;
      refreshToken = tokens.refreshToken;
      const ttlS = Math.round((tokens.expiresAt - Date.now()) / 1000);
      report(`授权码+token OK：ttl≈${ttlS}s；refresh_token=${maskSecret(tokens.refreshToken)}`);
    }

    // ── ② 原始 GET userinfo：探测新字段（键清单 + 值脱敏）───────────────
    const ui = await rawFetch(
      `${BASE_URL}/oauth2/userinfo`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
      30_000
    );
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(ui.bodyText) as Record<string, unknown>;
    } catch {
      parsed = { _raw: truncate(ui.bodyText) };
    }
    const rootKeys = Object.keys(parsed).sort();
    report(`userinfo HTTP ${ui.status}；顶层字段：${rootKeys.join(', ') || '(空)'}`);
    for (const k of rootKeys) {
      if (k === 'data' && parsed[k] && typeof parsed[k] === 'object') {
        const inner = parsed[k] as Record<string, unknown>;
        report(`userinfo.data.${k} → ${Object.keys(inner).sort().join(', ')}`);
      } else if (k !== 'sub' && k !== 'username' && k !== 'nickname') {
        report(`  ${k} = ${safeScalar(k, parsed[k])}`);
      }
    }

    // 字段可能平铺在 userinfo 顶层，也可能在 data 内，两层都找
    const nested = (parsed.data ?? {}) as Record<string, unknown>;
    const field = (name: string): unknown => {
      if (name in parsed) return parsed[name];
      if (name in nested) return nested[name];
      return undefined;
    };
    const encryptedApiKey = String(field('encryptedApiKey') ?? '');
    const aiGatewayStatus = String(field('aiGatewayStatus') ?? '');
    const configVersion = field('configVersion');
    const consumerId = String(field('consumerId') ?? '');

    report(`encryptedApiKey=${encryptedApiKey ? maskSecret(encryptedApiKey) : '(缺)'}`);
    report(
      `aiGatewayStatus=${aiGatewayStatus || '(缺)'}；configVersion=${safeScalar('configVersion', configVersion)}`
    );
    report(`consumerId=${consumerId ? maskSecret(consumerId) : '(缺)'}`);

    const keyMissing = encryptedApiKey === '';
    const notActive = aiGatewayStatus !== 'active';
    if (keyMissing || notActive) {
      report(
        `结论：encryptedApiKey 缺失=${keyMissing}；aiGatewayStatus='${aiGatewayStatus}'（期望 active）`
      );
      report('非网关问题，属平台侧开通/下发状态，先确认平台再跑（#922 依赖该字段）。');
      expect(
        false,
        `userinfo 未返回可用网关前提：encryptedApiKey=${keyMissing ? '缺失' : '存在'}, aiGatewayStatus='${aiGatewayStatus}'`
      ).toBe(true);
      return;
    }

    const keyMasked = maskSecret(encryptedApiKey);

    // ── ③ 网关实测：非流式（先按 #923 文档 body，若 4xx 提示缺参则补 max_tokens 再试）
    async function probeMessages(label: string, url: string, body: unknown): Promise<number> {
      try {
        const resp = await rawFetch(
          url,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Api-Key': encryptedApiKey },
            body: JSON.stringify(body),
          },
          60_000
        );
        const ct = resp.contentType ?? '';
        const looksJson = /json/i.test(ct);
        let detail = looksJson ? truncate(resp.bodyText) : truncate(resp.bodyText, 300);
        let extra = '';
        if (resp.status >= 400 && /(max_tokens|anthropic|stream)/i.test(resp.bodyText)) {
          extra = '（响应提示参数类错误）';
        }
        report(`${label} → HTTP ${resp.status} ct=${ct || '(无)'} ${detail}${extra}`);
        return resp.status;
      } catch (err) {
        report(`${label} → ERR ${err instanceof Error ? err.message : String(err)}`);
        return 0;
      }
    }

    const probeBody: Record<string, unknown> = {
      model: 'deepseek-v4-flash',
      messages: [{ role: 'user', content: 'ping' }],
    };
    const s1 = await probeMessages('非流式(#923 body)', GATEWAY_MESSAGES, probeBody);
    if (s1 >= 400 && s1 !== 0) {
      // 可能是 Anthropic /v1/messages 形态：补 max_tokens 再探一次
      await probeMessages('非流式(+max_tokens=64)', GATEWAY_MESSAGES, {
        ...probeBody,
        max_tokens: 64,
      });
    }

    // ── ③b 路由探测：进一步定位该前缀下还有哪些路径已挂路由 ────────────
    const candidatePaths = [
      `${GATEWAY_PREFIX}/v1/chat/completions`,
      `${GATEWAY_PREFIX}/v1/messages`,
      '/deepseek/v1/messages',
      '/v1/chat/completions',
    ];
    for (const p of candidatePaths) {
      await probeMessages(`路由探测 POST ${p}`, `${GATEWAY_BASE}${p}`, probeBody);
    }
    try {
      const root = await rawFetch(
        `${GATEWAY_BASE}/`,
        { headers: { 'X-Api-Key': encryptedApiKey } },
        20_000
      );
      report(`路由探测 GET / → HTTP ${root.status} ${truncate(root.bodyText, 200)}`);
    } catch (err) {
      report(`路由探测 GET / → ERR ${err instanceof Error ? err.message : String(err)}`);
    }

    // ── ④ SSE 流式：判别 Anthropic(event:data:) 还是 OpenAI(data:/[DONE]) ──
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 90_000);
      const res = await fetch(GATEWAY_MESSAGES, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Key': encryptedApiKey },
        body: JSON.stringify({ ...probeBody, stream: true }),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      let text = '';
      if (res.body) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          text += decoder.decode(value, { stream: true });
          if (text.length >= 64_000) {
            await reader.cancel();
            break;
          }
        }
      }
      const lines = text.split('\n').filter(Boolean);
      const dataLines = lines.filter((l) => l.startsWith('data:'));
      const eventLines = lines.filter((l) => l.startsWith('event:'));
      const doneMarker = dataLines.some((l) => l.includes('[DONE]'));
      const anthMarker = /message_start|content_block_delta|message_stop/i.test(text);
      report(`SSE → HTTP ${res.status} ct=${res.headers.get('content-type') || '(无)'}`);
      report(
        `SSE 形态：data 行=${dataLines.length}，event 行=${eventLines.length}，[DONE]=${doneMarker}，Anthropic 事件=${anthMarker}`
      );
      report(`SSE 前若干行：${truncate(lines.slice(0, 8).join(' ⏎ '), 1000)}`);
    } catch (err) {
      report(`SSE → ERR ${err instanceof Error ? err.message : String(err)}`);
    }

    // ── ⑤ https 变体：记录是否可达（明文 http 传 key 的安全结论）────────
    const httpsUrl = `${GATEWAY_MESSAGES.replace('http://', 'https://')}`;
    if (httpsUrl !== GATEWAY_MESSAGES) {
      await probeMessages('https 变体(#922 建议)', httpsUrl, probeBody);
    }

    // ── ⑥ 模型清单：{prefix}/v1/models → /models ─────────────────────────
    let listed = false;
    for (const p of [`${GATEWAY_PREFIX}/v1/models`, '/deepseek/v1/models', '/models']) {
      const modelsPath = `${GATEWAY_BASE}${p}`;
      try {
        const resp = await rawFetch(
          modelsPath,
          { headers: { 'X-Api-Key': encryptedApiKey } },
          30_000
        );
        if (resp.status === 200) {
          const j = JSON.parse(resp.bodyText) as { data?: unknown; models?: unknown };
          const ids = Array.isArray(j.data)
            ? (j.data as Array<{ id?: string }>).map((m) => String(m.id ?? ''))
            : Array.isArray(j.models)
              ? (j.models as string[])
              : [];
          report(
            `模型清单 GET ${modelsPath} → HTTP 200：${ids.length ? ids.join(', ') : truncate(resp.bodyText)}`
          );
          listed = true;
          break;
        }
        report(`模型清单 GET ${modelsPath} → HTTP ${resp.status} ${truncate(resp.bodyText, 300)}`);
      } catch (err) {
        report(
          `模型清单 GET ${modelsPath} → ERR ${err instanceof Error ? err.message : String(err)}`
        );
      }
    }
    if (!listed) {
      report('模型清单：无可用 /models 接口（结论需注明），仅以非流式探测的 model 可用性为准。');
    }

    // ── ⑦ 刷新探针（可选）：记录生产 refresh_token 是否轮换 ──────────────
    if (refreshToken) {
      try {
        const refreshed = await client.refreshTokens(CONFIG, refreshToken);
        const rotated = refreshed.refreshToken !== refreshToken;
        report(
          `refresh OK（refresh_token ${rotated ? '已轮换' : '未轮换'}：${maskSecret(refreshed.refreshToken)}）`
        );
      } catch (err) {
        report(
          `refresh → ERR ${err instanceof Error ? ((err as Error & { code?: string }).code ?? err.message) : String(err)}`
        );
      }
    }

    report(`key=${keyMasked}（只用于本次进程内探测，未落盘/未入库）`);
    // 前提已满足：到此所有探测结果均已记录，测试通过（各探测结论以 [gateway] 行作准）。
    expect(encryptedApiKey.length).toBeGreaterThan(0);
    expect(aiGatewayStatus).toBe('active');
  }, 240_000);
});
