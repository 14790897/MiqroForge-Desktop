/**
 * MiQroForge 平台账号登录（issue #726）。
 *
 * 设置页内的登录入口：手机号 + 密码（密码仅经 IPC 提交给主进程，
 * 前端不落任何存储、不打日志）→ 主进程完成平台登录 + 授权码流程 +
 * token 换取与安全存储。登录后展示账号信息与 token 到期/刷新时间，
 * 刷新失败时引导重新登录。
 */

import { useState, useEffect, useCallback } from 'react';
import {
  CloudCog,
  LogIn,
  LogOut,
  RefreshCw,
  Eye,
  EyeOff,
  ChevronDown,
  UserRound,
  ShieldCheck,
  TriangleAlert,
  CheckCircle2,
  BadgeInfo,
  Globe,
  Coins,
} from 'lucide-react';
import { Button } from '../../../components/ui/Button';
import { Input } from '../../../components/ui/Input';
import { cn } from '../../../lib/utils';
import type {
  QraftBillingHistoryEntry,
  QraftErrorCode,
  QraftLoginResult,
  QraftStatus,
} from '../../../../shared/ipc';

/** 各错误码对应的修复指引（服务端 message 优先展示，这里只兜底）。 */
const ERROR_GUIDANCE: Partial<Record<QraftErrorCode, string>> = {
  IP_NOT_WHITELISTED:
    '出口 IP 未加白：本机出口 IP 不在 MiQroForge 平台白名单内，请联系 MiQroForge 管理员加白后重试。',
  NETWORK_UNREACHABLE:
    '网络请求失败（多次重试后仍超时）。请检查网络连接后重试；如持续失败可能是出口线路抖动。',
  PUBLIC_KEY_EXTRACT_FAILED:
    '无法从 MiQroForge 登录页前端 bundle 提取 RSA 公钥。请确认 MiQroForge 基础地址正确、当前网络可访问登录页。',
  SESSION_EXPIRED: 'MiQroForge 登录态已失效，请重新登录。',
  AUTHORIZE_FAILED: '授权流程失败。可尝试退出后重新登录；如反复出现请查看日志排查。',
  TOKEN_EXCHANGE_FAILED: '换取 token 失败。可尝试重新登录；如反复出现请查看日志排查。',
  REFRESH_FAILED: 'token 刷新失败，登录已过期，请重新登录。',
  REFRESH_TOKEN_INVALID:
    'refresh_token 已失效（平台升级或安全策略变更可能导致登录态作废），请重新登录。',
  USERINFO_FAILED: '获取用户信息失败（不影响已登录状态）。',
  LOGIN_CANCELLED: '已取消：登录窗口在完成授权前被关闭。',
  BROWSER_LOGIN_FAILED:
    '浏览器登录失败：无法打开 MiQroForge 登录页或等待授权超时，请检查网络后重试。',
  INVALID_CONFIG: '接入配置不完整或非法，请检查高级设置中的 client_secret 等项。',
  INTERNAL: '发生未知错误，请查看日志排查。',
};

function maskPhone(phone: string): string {
  if (!phone) return '';
  if (phone.length <= 7) return `${phone.slice(0, 3)}****`;
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
}

function fmtDateTime(epochMs?: number): string {
  if (!epochMs) return '—';
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function errorText(result: QraftLoginResult | null, fallback: string): string {
  if (!result) return fallback;
  if (result.message) return result.message;
  return ERROR_GUIDANCE[result.code ?? 'INTERNAL'] ?? fallback;
}

/** AI 网关状态展示文案（#922）。 */
function gatewayStatusText(status: string): { label: string; hint: string } {
  switch (status) {
    case 'active':
      return {
        label: '可用',
        hint: 'AI 网关已开通：模型调用将走平台网关，计入平台消费组配额与计费。',
      };
    case 'provisioning':
      return { label: '开通中', hint: 'AI 网关正在开通，暂时无法发起会话。请稍后刷新查看。' };
    case 'failed':
      return { label: '开通失败', hint: 'AI 网关开通失败，请联系平台处理后再试。' };
    case 'disabled':
      return { label: '已停用', hint: 'AI 网关已停用，请联系平台启用后再试。' };
    default:
      return { label: status || '未知', hint: 'AI 网关状态未知，请联系平台确认。' };
  }
}

const ModeBtn = ({
  value,
  current,
  set,
  label,
}: {
  value: 'test' | 'prod';
  current: 'test' | 'prod';
  set: (v: 'test' | 'prod') => void;
  label: string;
}) => (
  <button
    onClick={() => set(value)}
    className={cn(
      'settings-hover-tab px-3 py-1.5 rounded-lg text-body-sm border',
      current === value
        ? 'bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]'
        : 'bg-[var(--surface)] text-[var(--text-muted)] border-[var(--border)] hover:border-[var(--accent)]'
    )}
  >
    {label}
  </button>
);

export function QraftPage() {
  const [status, setStatus] = useState<QraftStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [env, setEnv] = useState<'test' | 'prod'>('test');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [redirectUri, setRedirectUri] = useState('');

  const [loggingIn, setLoggingIn] = useState(false);
  const [browserLoggingIn, setBrowserLoggingIn] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [browserNotice, setBrowserNotice] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [pointsLoading, setPointsLoading] = useState(false);
  const [pointsError, setPointsError] = useState<string | null>(null);
  /** 扣费历史（Slurm 作业扣费记录，issue #927）。 */
  const [billingHistory, setBillingHistory] = useState<QraftBillingHistoryEntry[] | null>(null);
  /** pointsBalance IPC 的本地结果（status.points 未推送时兜底展示）。 */
  const [fetchedPoints, setFetchedPoints] = useState<{
    availablePoints: number;
    totalEarned: number;
    totalSpent: number;
  } | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await window.miqi.qraft.status());
    } catch {
      /* IPC 未就绪时保持空状态 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    // 旧版 preload（如 smoke mock）可能没有 qraft 命名空间，防御性处理。
    let unsubscribe: (() => void) | undefined;
    try {
      unsubscribe = window.miqi.qraft?.onStatusChanged((next) => setStatus(next));
    } catch {
      /* 状态事件不可用时仅依赖主动查询 */
    }
    return () => unsubscribe?.();
  }, [loadStatus]);

  const handleLogin = async () => {
    setLoggingIn(true);
    setLoginError(null);
    try {
      if (!phone.trim()) {
        setLoginError('请输入手机号');
        return;
      }
      if (!password) {
        setLoginError('请输入密码');
        return;
      }
      const result = await window.miqi.qraft.login(phone.trim(), password, {
        env,
        baseUrl: baseUrl.trim() || undefined,
        clientId: clientId.trim() || undefined,
        clientSecret: clientSecret.trim() || undefined,
        redirectUri: redirectUri.trim() || undefined,
      });
      if (result.ok) {
        setPassword('');
        setStatus(await window.miqi.qraft.status());
      } else {
        setLoginError(errorText(result, '登录失败'));
      }
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'IPC 调用失败');
    } finally {
      setLoggingIn(false);
    }
  };

  /** 浏览器登录：打开 MiQroForge 授权页，用户在页面完成登录并点击"同意"。 */
  const handleBrowserLogin = async () => {
    setBrowserLoggingIn(true);
    setLoginError(null);
    setBrowserNotice(null);
    try {
      const result = await window.miqi.qraft.browserLogin({
        env,
        baseUrl: baseUrl.trim() || undefined,
        clientId: clientId.trim() || undefined,
        clientSecret: clientSecret.trim() || undefined,
        redirectUri: redirectUri.trim() || undefined,
      });
      if (result.ok) {
        setPassword('');
        setStatus(await window.miqi.qraft.status());
      } else if (result.code === 'LOGIN_CANCELLED') {
        setBrowserNotice(errorText(result, '已取消浏览器登录'));
      } else {
        setLoginError(errorText(result, '浏览器登录失败'));
      }
    } catch (e) {
      setLoginError(e instanceof Error ? e.message : 'IPC 调用失败');
    } finally {
      setBrowserLoggingIn(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      const result = await window.miqi.qraft.refresh();
      if (!result.ok) setRefreshError(errorText(result, '刷新失败'));
      setStatus(await window.miqi.qraft.status());
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : 'IPC 调用失败');
    } finally {
      setRefreshing(false);
    }
  };

  const handleLogout = async () => {
    setRefreshError(null);
    setLoginError(null);
    try {
      await window.miqi.qraft.logout();
      setStatus(await window.miqi.qraft.status());
    } catch {
      /* 忽略退出失败，页面会跟随状态事件更新 */
    }
  };

  /** 拉取最新积分余额；未登录（INVALID_CONFIG）时静默。 */
  const loadPoints = useCallback(async () => {
    setPointsLoading(true);
    setPointsError(null);
    try {
      const result = await window.miqi.qraft.pointsBalance();
      if (result.ok) {
        setFetchedPoints(result.points);
      } else if (result.code !== 'INVALID_CONFIG') {
        setPointsError(result.message || '积分余额获取失败');
      }
    } catch {
      /* IPC 未就绪时保持空状态 */
    } finally {
      setPointsLoading(false);
    }
  }, []);

  // 登录后拉取一次余额；此后余额经 qraft:statusChanged 事件随
  // status.points 更新。
  useEffect(() => {
    if (status?.loggedIn === true) {
      void loadPoints();
    }
  }, [status?.loggedIn, loadPoints]);

  // 登录后拉取扣费历史；扣费结果（billed/insufficient/error）会推送
  // 新余额——余额变化时重拉历史，保证计费结果即时可见（页面挂载期间
  // 扣费发生时旧快照不会停留在列表里）。
  useEffect(() => {
    if (status?.loggedIn === true) {
      void window.miqi.qraft
        .billingHistory()
        .then(setBillingHistory)
        .catch(() => setBillingHistory([]));
    } else {
      setBillingHistory(null);
    }
  }, [status?.loggedIn, status?.points?.availablePoints]);

  if (loading) return null;

  const loggedIn = status?.loggedIn === true;
  const account = status?.account;
  const needsRelogin = status?.requiresRelogin === true;
  const points = status?.points ?? fetchedPoints;

  return (
    <div className="p-6 max-w-lg flex flex-col gap-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent)]">
          <CloudCog size={18} />
        </div>
        <div className="min-w-0">
          <h3 className="text-subheading text-[var(--text)]">MiQroForge 平台账号</h3>
          <p className="mt-1 text-xs leading-relaxed text-[var(--text-faint)]">
            登录后 MiQroForge 将以你的身份调用 MiQroForge 平台接口（授权码流程，凭据安全存储，
            到期自动刷新）。推荐使用浏览器登录：打开 MiQroForge 平台页面完成登录并点击
            「同意」，MiQroForge 自动完成授权。
          </p>
        </div>
      </div>

      {!loggedIn ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void handleLogin();
          }}
        >
          {/* 浏览器登录（MiQroForge 授权页修复后可用：用户在页面点击"同意"） */}
          <div className="flex flex-col gap-1.5">
            <Button
              type="button"
              variant="outline"
              onClick={handleBrowserLogin}
              disabled={browserLoggingIn}
              className="justify-center"
              data-testid="qraft-browser-login-btn"
            >
              {browserLoggingIn ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <Globe size={14} />
              )}
              {browserLoggingIn
                ? '等待授权中…（请在 MiQroForge 页面完成登录）'
                : '浏览器登录（推荐）'}
            </Button>
            <p className="text-size-2xs text-[var(--text-faint)]">
              将打开 MiQroForge 平台授权页，在页面完成登录并点击「同意」后自动回到 MiQroForge。
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-[var(--border-subtle)]" />
            <span className="text-size-2xs text-[var(--text-faint)]">或使用手机号登录</span>
            <div className="h-px flex-1 bg-[var(--border-subtle)]" />
          </div>

          {/* 环境选择 */}
          <div className="flex flex-col gap-1.5">
            <label className="text-size-sm font-medium text-[var(--text-muted)]">环境</label>
            <div className="flex gap-2">
              <ModeBtn value="test" current={env} set={setEnv} label="测试环境" />
              <ModeBtn value="prod" current={env} set={setEnv} label="生产环境" />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="qraft-phone"
              className="text-size-sm font-medium text-[var(--text-muted)]"
            >
              手机号
            </label>
            <Input
              id="qraft-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="MiQroForge 平台账号手机号"
              autoComplete="username"
              inputMode="numeric"
              data-testid="qraft-phone-input"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="qraft-password"
              className="text-size-sm font-medium text-[var(--text-muted)]"
            >
              密码
            </label>
            <div className="flex gap-2">
              <Input
                id="qraft-password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="MiQroForge 平台密码"
                autoComplete="current-password"
                className="flex-1"
                data-testid="qraft-password-input"
              />
              <Button
                variant="ghost"
                size="icon"
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? '隐藏密码' : '显示密码'}
              >
                {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
              </Button>
            </div>
          </div>

          {/* 高级设置 */}
          <div className="border-t border-[var(--border-subtle)] pt-3">
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="flex w-full items-center gap-1.5 text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
            >
              <ChevronDown
                size={12}
                className={cn('transition-transform duration-150', advancedOpen && 'rotate-180')}
              />
              高级设置（接入配置，默认按环境预填）
            </button>
            {advancedOpen && (
              <div className="mt-3 flex flex-col gap-3">
                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="qraft-baseurl"
                    className="text-size-xs font-medium text-[var(--text-faint)]"
                  >
                    API 基础地址（留空用环境默认）
                  </label>
                  <Input
                    id="qraft-baseurl"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://test.forge.miqroera.com/api"
                    className="font-mono text-xs"
                    data-testid="qraft-baseurl-input"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <label
                      htmlFor="qraft-client-id"
                      className="text-size-xs font-medium text-[var(--text-faint)]"
                    >
                      client_id
                    </label>
                    <Input
                      id="qraft-client-id"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      placeholder="miqi"
                      className="font-mono text-xs"
                      data-testid="qraft-client-id-input"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label
                      htmlFor="qraft-client-secret"
                      className="text-size-xs font-medium text-[var(--text-faint)]"
                    >
                      client_secret
                    </label>
                    <Input
                      id="qraft-client-secret"
                      type={showPassword ? 'text' : 'password'}
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      placeholder="留空用默认值（测试阶段）"
                      className="font-mono text-xs"
                      data-testid="qraft-client-secret-input"
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="qraft-redirect-uri"
                    className="text-size-xs font-medium text-[var(--text-faint)]"
                  >
                    redirect_uri（留空自动生成 loopback 地址）
                  </label>
                  <Input
                    id="qraft-redirect-uri"
                    value={redirectUri}
                    onChange={(e) => setRedirectUri(e.target.value)}
                    placeholder="http://localhost:<随机端口>/callback"
                    className="font-mono text-xs"
                    data-testid="qraft-redirect-uri-input"
                  />
                </div>
                <p className="text-size-2xs text-[var(--text-faint)]">
                  生产环境必须使用在 MiQroForge 平台注册的回调地址；测试环境不校验注册值。
                </p>
              </div>
            )}
          </div>

          {browserNotice && (
            <div
              className="flex items-start gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5 text-xs leading-relaxed text-[var(--text-muted)]"
              data-testid="qraft-browser-notice"
            >
              <BadgeInfo size={14} className="mt-0.5 shrink-0" />
              <span className="min-w-0">{browserNotice}</span>
            </div>
          )}

          {loginError && (
            <div
              className="flex items-start gap-2 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger-bg)] px-3 py-2.5 text-xs leading-relaxed text-[var(--danger)]"
              data-testid="qraft-login-error"
            >
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
              <span className="min-w-0">{loginError}</span>
            </div>
          )}

          <Button
            type="submit"
            disabled={loggingIn}
            className="self-start"
            data-testid="qraft-login-btn"
          >
            {loggingIn ? <RefreshCw size={14} className="animate-spin" /> : <LogIn size={14} />}
            {loggingIn ? '登录中…（含授权流程，请稍候）' : '登录'}
          </Button>
        </form>
      ) : (
        <div className="flex flex-col gap-4">
          {needsRelogin && (
            <div
              className="flex items-start gap-2 rounded-lg border border-[var(--warning)]/50 bg-[var(--warning)]/10 px-3 py-2.5 text-xs leading-relaxed text-[var(--warning)]"
              data-testid="qraft-relogin-banner"
            >
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
              <span>
                登录已过期（token 刷新失败），部分平台功能不可用。请重新登录以恢复 MiQroForge
                平台能力。
              </span>
            </div>
          )}

          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface)] p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]">
                <UserRound size={18} />
              </div>
              <div className="min-w-0">
                <p className="text-size-sm font-semibold text-[var(--text)]">
                  {account?.nickname || account?.username || 'MiQroForge 用户'}
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-size-2xs font-medium text-emerald-600">
                    <CheckCircle2 size={10} />
                    已登录
                  </span>
                </p>
                <p className="mt-0.5 text-size-2xs text-[var(--text-faint)]">
                  {account?.username && `用户名 ${account.username} · `}
                  {account?.phone ? `手机号 ${maskPhone(account.phone)} · ` : ''}环境{' '}
                  {status?.env === 'prod' ? '生产' : '测试'}
                </p>
              </div>
            </div>

            <dl className="mt-4 grid grid-cols-1 gap-2 border-t border-[var(--border-subtle)] pt-3 text-size-2xs sm:grid-cols-2">
              <div className="flex items-center gap-2 text-[var(--text-muted)]">
                <ShieldCheck size={12} className="shrink-0 text-[var(--text-faint)]" />
                <dt>access_token 到期：</dt>
                <dd className="font-mono text-[var(--text)]">{fmtDateTime(status?.expiresAt)}</dd>
              </div>
              <div className="flex items-center gap-2 text-[var(--text-muted)]">
                <RefreshCw size={12} className="shrink-0 text-[var(--text-faint)]" />
                <dt>计划自动刷新：</dt>
                <dd className="font-mono text-[var(--text)]">
                  {fmtDateTime(status?.refreshScheduledAt)}
                </dd>
              </div>
            </dl>
            <p className="mt-2 flex items-center gap-1.5 text-size-2xs text-[var(--text-faint)]">
              <BadgeInfo size={11} />
              实测 access_token 有效期约 2 小时，MiQroForge 会在到期前 15 分钟自动刷新。
            </p>

            {/* AI 网关状态：#922 —— active 才允许模型调用走网关 */}
            {status?.aiGateway &&
              (() => {
                const gw = gatewayStatusText(status.aiGateway.status);
                return (
                  <div
                    className="mt-4 border-t border-[var(--border-subtle)] pt-3"
                    data-testid="qraft-ai-gateway"
                  >
                    <div className="flex items-center gap-2 text-size-sm text-[var(--text-muted)]">
                      <ShieldCheck size={14} className="shrink-0 text-[var(--accent)]" />
                      <span className="text-[var(--text-muted)]">AI 网关</span>
                      <span
                        className="font-mono text-[var(--text)]"
                        data-testid="qraft-ai-gateway-status"
                      >
                        {gw.label}
                      </span>
                      {status.aiGateway.configVersion != null && (
                        <span className="ml-auto text-size-2xs text-[var(--text-faint)]">
                          配置版本 v{status.aiGateway.configVersion}
                        </span>
                      )}
                    </div>
                    <p className="mt-1.5 text-size-2xs leading-relaxed text-[var(--text-faint)]">
                      {gw.hint}
                    </p>
                  </div>
                );
              })()}

            {/* 积分余额：Slurm MCP 作业每次运行扣 10 分，普通对话与本地任务不扣分 */}
            <div
              className="mt-4 border-t border-[var(--border-subtle)] pt-3"
              data-testid="qraft-points-balance"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-size-sm text-[var(--text-muted)]">
                  <Coins size={14} className="shrink-0 text-[var(--accent)]" />
                  <dt>可用积分</dt>
                  <dd
                    className="font-mono text-base font-semibold text-[var(--text)]"
                    data-testid="qraft-points-value"
                  >
                    {pointsLoading && points === null ? '…' : (points?.availablePoints ?? '—')}
                  </dd>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => void loadPoints()}
                  disabled={pointsLoading}
                  title="刷新积分余额"
                  aria-label="刷新积分余额"
                  data-testid="qraft-points-refresh-btn"
                >
                  <RefreshCw size={13} className={pointsLoading ? 'animate-spin' : ''} />
                </Button>
              </div>
              <p className="mt-1.5 text-size-2xs leading-relaxed text-[var(--text-faint)]">
                Slurm MCP 作业每次运行消耗 10 积分；普通对话与本地任务不扣积分。
                {points !== null &&
                  ` 累计获得 ${points.totalEarned}，累计支出 ${points.totalSpent}。`}
              </p>
              {pointsError && (
                <p className="mt-1 text-size-2xs text-[var(--danger)]">{pointsError}</p>
              )}

              {/* 扣费历史（issue #927：Slurm 作业扣费记录，本地留存可追溯） */}
              {billingHistory !== null && billingHistory.length > 0 && (
                <div
                  className="mt-3 border-t border-[var(--border-subtle)] pt-3"
                  data-testid="qraft-billing-history"
                >
                  <p className="mb-1.5 text-size-2xs font-medium text-[var(--text-muted)]">
                    扣费历史（Slurm 作业）
                  </p>
                  <ul className="flex max-h-56 flex-col gap-1.5 overflow-y-auto">
                    {billingHistory.map((entry) => (
                      <li
                        key={entry.chargeId}
                        className="flex items-center justify-between gap-2 text-size-2xs"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[var(--text)]">
                            {entry.jobId
                              ? `作业 ${entry.jobId}`
                              : `${entry.serverName ?? ''}.${entry.toolName ?? ''}`}
                            <span className="ml-2 text-[var(--text-faint)]">
                              {fmtDateTime(Date.parse(entry.deductedAt))}
                            </span>
                          </p>
                          {entry.argsSummary && (
                            <p className="truncate text-[var(--text-faint)]">{entry.argsSummary}</p>
                          )}
                        </div>
                        <div className="shrink-0 text-right">
                          {entry.status === 'billed' ? (
                            <>
                              <span className="text-[var(--danger)]">-{entry.cost}</span>
                              {entry.balanceAfter !== undefined && (
                                <span className="ml-1 text-[var(--text-faint)]">
                                  余额 {entry.balanceAfter}
                                </span>
                              )}
                            </>
                          ) : (
                            <span className="text-[var(--warning)]">
                              {entry.status === 'insufficient' ? '余额不足' : '扣费失败'}
                            </span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>

          {refreshError && (
            <div
              className="flex items-start gap-2 rounded-lg border border-[var(--danger)]/40 bg-[var(--danger-bg)] px-3 py-2.5 text-xs leading-relaxed text-[var(--danger)]"
              data-testid="qraft-refresh-error"
            >
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
              <span>{refreshError}</span>
            </div>
          )}

          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshing}
              data-testid="qraft-refresh-btn"
            >
              {refreshing ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              立即刷新
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleLogout}
              className="text-[var(--danger)] border-[var(--danger)] hover:bg-[var(--danger)]/10"
              data-testid="qraft-logout-btn"
            >
              <LogOut size={14} />
              退出登录
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
