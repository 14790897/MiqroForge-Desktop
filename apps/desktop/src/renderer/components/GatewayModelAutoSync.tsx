import { useEffect, useRef } from 'react';
import { invalidateConfigCache } from '../lib/configCache';
import { GATEWAY_MODEL_ID } from '../features/providers/components/ModelQuickPanel';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { useRuntime } from '../contexts/RuntimeContext';

/**
 * 登录后 AI 网关自动生效（#922 收尾 / #1172）。
 *
 * 网关不是登录即启用的开关：运行时只在默认模型恰好是网关实测模型时才会
 * 把调用改道到平台网关（miqi/providers/factory.py）。登录本身不会写默认
 * 模型，用户会卡在「已开通但模型未设置」的状态。
 *
 * 判「未设置」不能只看默认模型是否为空：config.get 会把 Python schema 的
 * 默认值 anthropic/claude-opus-4-5 一并带出来（miqi/runtime/config_handlers.py
 * 的 model_dump(by_alias=True)），全新安装下也永远非空 —— 原来「只填空值」
 * 的条件在生产中从不成立，自动逻辑等于不存在（#1172 实测）。这里改为按
 * 「可用性」判定：当前默认模型确实由它自己的 provider（凭据齐备）或平台
 * AI 网关路由时才认作「用户已选好」并保持不动，否则自动写成网关模型。
 *
 * 判据取自 providers.list 的 active_model_own_or_gateway_resolvable（严格版），
 * 而不是发送门禁用的 active_model_resolvable：后者含「已配置 gateway 型
 * provider 兜底」，存量配置里留着的 siliconflow/openrouter 旧 key 会让 schema
 * 默认值经兜底被判成可用，从而盖住自动写入。
 */

/**
 * 单次尝试的超时。
 *
 * 桥下调用可能一直不 settle：桥热重启时旧进程的 close 处理因
 * `this.process !== bridgeProcess` 提前返回（apps/desktop/src/main/bridge.ts），
 * 挂在旧进程上的 pending 请求既不会被 reject 也不会被 resolve。启动即登录
 * （重启应用 / 预置登录态）时自动就绪正好撞在这个窗口上，一次调用卡死就
 * 再也不会重来 —— 表现为「登录后默认模型始终不就绪」。因此这里给每次尝试
 * 加超时并退避重试，让被孤儿化的调用不至于静默吞掉整个自动就绪。
 */
const ATTEMPT_TIMEOUT_MS = 5_000;
/** 首次 + 重试的总次数。 */
const MAX_ATTEMPTS = 4;
/** 退避基数：第 n 次失败后等 n * RETRY_DELAY_MS 再试。 */
const RETRY_DELAY_MS = 1_000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('gateway model auto-sync timed out')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** 读 config 快照里的当前默认模型；形态非法或未设置时返回空串。 */
export function currentDefaultModel(config: unknown): string {
  if (!config || typeof config !== 'object') return '';
  const agents = (config as Record<string, unknown>).agents;
  if (!agents || typeof agents !== 'object') return '';
  const defaults = (agents as Record<string, unknown>).defaults;
  if (!defaults || typeof defaults !== 'object') return '';
  const model = (defaults as Record<string, unknown>).model;
  return typeof model === 'string' ? model.trim() : '';
}

/**
 * 当前默认模型需要自动兜底为网关模型时返回该 id，否则返回 null。
 *
 * ownOrGatewayResolvable 为 undefined 表示旧版 bridge 不返回该字段：此时只
 * 保留「空值兜底」的老行为，非空值一律不动 —— 拿不到判据就不改写用户配置。
 */
export function gatewayModelToAutoSet(
  current: string,
  ownOrGatewayResolvable: boolean | undefined
): string | null {
  if (!current) return GATEWAY_MODEL_ID;
  if (current === GATEWAY_MODEL_ID) return null;
  if (ownOrGatewayResolvable === undefined) return null;
  return ownOrGatewayResolvable ? null : GATEWAY_MODEL_ID;
}

/** 保存结果：saved=false 表示后端因期望值不匹配跳过（用户已在间隙选了模型）。 */
interface ConfigUpdateResult {
  saved: boolean;
  skipped?: string;
}

/**
 * 从桥错误里取回错误码（bridge.ts 抛 `new Error("<message> (<CODE>)")`）。
 *
 * Electron 的 `ipcRenderer.invoke` 只把 message 透给渲染进程，自定义的
 * `.code` 属性会在序列化时丢掉，所以只能从文案尾部取（现有契约，用户报错
 * 里那句 `… (INVALID_PARAMS)` 就是它）。
 */
export function bridgeErrorCode(err: unknown): string | null {
  const message = err instanceof Error ? err.message : String(err ?? '');
  // 取最后一个全大写括号标记：桥错误固定以 `(CODE)` 结尾，但外层还会套 Electron
  // 的 "Error invoking remote method …" 前缀/后缀，端点锚定并不可靠。
  const matches = [...message.matchAll(/\(([A-Z][A-Z0-9_]{2,})\)/g)];
  return matches.length ? matches[matches.length - 1][1] : null;
}

/**
 * 自动同步的保存动作（独立导出以便无 DOM 单测，#991 review）。
 *
 * 用当前模型值做 expectModel 比较并设置：后端只在磁盘上的默认模型仍与快照
 * 一致时写入。读取快照与写入之间用户若已手动改了模型，后端返回 saved=false，
 * 这里直接放弃，保留用户更新的选择。
 *
 * isEligible 在写入前再查一次（仍在「登录 + 网关 active」才允许写）：读快照
 * 与查 provider 列表都带 await，期间用户完全可能已经登出或网关失效，而后端
 * 的比较并设置只看模型值，察觉不到这种变化。
 *
 * resyncToken（#1258）：后端以 GATEWAY_CREDS_UNAVAILABLE 拒绝时，说明它读的
 * 那份握手文件 `<workspace>/.qraft/token.json` 不可用 —— 而渲染进程判定
 * 「网关可用」用的是内存里的登录态，两者从不校验一致性。此时先重新同步一次
 * 握手文件再重试一次：这条路径曾把用户挡在「Unsupported model」外面，且
 * 重试全败后静默放弃（#1258 真实用户两次遇到）。
 */
export async function saveGatewayModelIfUnusable(
  getConfig: () => Promise<unknown>,
  listProviders: () => Promise<{ active_model_own_or_gateway_resolvable?: boolean }>,
  updateConfig: (
    config: Record<string, unknown>,
    expectModel?: string
  ) => Promise<ConfigUpdateResult | unknown>,
  invalidate: () => void,
  isEligible: () => boolean,
  resyncToken?: () => Promise<{ ok: boolean; message?: string }>
): Promise<void> {
  const config = await getConfig();
  const current = currentDefaultModel(config);
  if (current === GATEWAY_MODEL_ID) return;
  // 未设置时无需查 provider 列表：直接走空值兜底（旧版 bridge 也保持该行为）。
  let ownOrGatewayResolvable: boolean | undefined;
  if (current) {
    const providers = await listProviders();
    ownOrGatewayResolvable = providers?.active_model_own_or_gateway_resolvable;
  }
  const modelId = gatewayModelToAutoSet(current, ownOrGatewayResolvable);
  if (!modelId) return;
  if (!isEligible()) return; // 登出 / 网关失效 → 不写
  const payload = { agents: { defaults: { model: modelId } } };
  let result: ConfigUpdateResult | unknown;
  try {
    result = await updateConfig(payload, current);
  } catch (err) {
    // 握手文件没到位：重新同步一次再重试一次；同步本身失败（如 .qraft 写不进去）
    // 就保留后端语义，交给上层上报，不再假装成功。
    if (bridgeErrorCode(err) !== 'GATEWAY_CREDS_UNAVAILABLE' || !resyncToken) throw err;
    if (!isEligible()) throw err;
    const synced = await resyncToken();
    if (!synced?.ok) throw err;
    result = await updateConfig(payload, current);
  }
  if (result && typeof result === 'object' && (result as ConfigUpdateResult).saved === false) {
    return; // 被比较并设置拦截：用户的选择优先
  }
  invalidate();
}

export interface AutoSyncOptions {
  attempts?: number;
  timeoutMs?: number;
  retryDelayMs?: number;
}

/**
 * 带单次超时与退避重试地执行一次自动同步（导出以便无 DOM 单测）。
 *
 * 返回 true 表示某次尝试正常返回（含「无需写入」）。每次尝试前都与重试之间
 * 复查 isEligible：用户中途登出 / 网关失效就立刻停手。全部尝试都没返回时
 * 返回 false，由调用方决定是否等下一次状态变化再试。
 */
export async function autoSyncGatewayModel(
  save: () => Promise<void>,
  isEligible: () => boolean,
  opts: AutoSyncOptions = {}
): Promise<boolean> {
  const attempts = opts.attempts ?? MAX_ATTEMPTS;
  const timeoutMs = opts.timeoutMs ?? ATTEMPT_TIMEOUT_MS;
  const retryDelayMs = opts.retryDelayMs ?? RETRY_DELAY_MS;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    if (!isEligible()) return false;
    try {
      await withTimeout(save(), timeoutMs);
      return true;
    } catch {
      // 桥未就绪，或调用被热重启孤儿化（见 ATTEMPT_TIMEOUT_MS）→ 退避重试
    }
    if (!isEligible()) return false;
    if (attempt < attempts) await delay(retryDelayMs * attempt);
  }
  return false;
}

export function GatewayModelAutoSync() {
  const { loggedIn, gatewayActive } = useQraftStatus();
  const { status } = useRuntime();
  const attemptedRef = useRef(false);
  // 重试期间用户登出 / 网关失效 → 立即停手，别把网关模型写给未登录用户。
  const eligibleRef = useRef(false);
  // 最后一次失败原因：放弃重试后要留下一句能查的记录，不能再静默（#1258）。
  const failureRef = useRef<string | null>(null);

  useEffect(() => {
    eligibleRef.current = loggedIn && gatewayActive;
  }, [loggedIn, gatewayActive]);

  useEffect(() => {
    if (!eligibleRef.current) {
      attemptedRef.current = false; // 退出登录/网关不可用后，下次登录允许重试
      return;
    }
    if (status.state !== 'running' || attemptedRef.current) return;
    attemptedRef.current = true;

    void autoSyncGatewayModel(
      async () => {
        failureRef.current = null;
        try {
          await saveGatewayModelIfUnusable(
            () => window.miqi.config.get(),
            () => window.miqi.providers.list(),
            (config, expectModel) => window.miqi.config.update(config, expectModel),
            invalidateConfigCache,
            () => eligibleRef.current,
            // 网关凭据握手文件没到位时的补救通道（#1258）。
            () => window.miqi.qraft.syncToken()
          );
        } catch (err) {
          failureRef.current = err instanceof Error ? err.message : String(err);
          throw err;
        }
      },
      () => eligibleRef.current
    ).then((ok) => {
      if (ok) return;
      // 全败后以前只复位 attemptedRef —— 用户侧「默认模型始终不就绪」连一条
      // 可查原因都没有（#1258）。
      console.error(
        `[gateway] 网关默认模型自动就绪失败：${failureRef.current ?? '桥未在超时内响应'}`
      );
      attemptedRef.current = false; // 未成功 → 等下一次状态变化再试
    });
  }, [loggedIn, gatewayActive, status.state]);

  return null;
}
