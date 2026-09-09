/**
 * MiQroForge 平台账号登录（issue #726）。
 *
 * 设置页内的登录入口：浏览器 OAuth 登录 —— 主进程打开 MiQroForge 授权页，
 * 用户在平台页面完成登录并点击「同意」，授权码由 IPC 层拦截后换 token，
 * 凭据安全存储。登录后展示账号信息与 token 到期/刷新时间，刷新失败时
 * 引导重新登录。
 */

import { useState, useEffect, useCallback } from 'react';
import {
  CloudCog,
  LogOut,
  RefreshCw,
  UserRound,
  ShieldCheck,
  TriangleAlert,
  CheckCircle2,
  BadgeInfo,
  Globe,
  Coins,
} from 'lucide-react';
import { Button } from '../../../components/ui/Button';
import { gatewayStatusText } from '../../../lib/qraftGateway';
import { qraftErrorText } from '../../../lib/qraftErrors';
import type {
  QraftBillingHistoryEntry,
  QraftLoginResult,
  QraftStatus,
} from '../../../../shared/ipc';

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

export function QraftPage() {
  const [status, setStatus] = useState<QraftStatus | null>(null);
  const [loading, setLoading] = useState(true);

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

  /** 浏览器登录：打开 MiQroForge 授权页，用户在页面完成登录并点击"同意"。 */
  const handleBrowserLogin = async () => {
    setBrowserLoggingIn(true);
    setLoginError(null);
    setBrowserNotice(null);
    try {
      const result = await window.miqi.qraft.browserLogin({
        env: 'test',
      });
      if (result.ok) {
        setStatus(await window.miqi.qraft.status());
      } else if (result.code === 'LOGIN_CANCELLED') {
        setBrowserNotice(qraftErrorText(result, '已取消浏览器登录'));
      } else {
        setLoginError(qraftErrorText(result, '浏览器登录失败'));
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
      if (!result.ok) setRefreshError(qraftErrorText(result, '刷新失败'));
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
            登录后 MiQroForge 将以你的身份调用 MiQroForge 平台接口（OAuth 授权码流程，
            凭据安全存储，到期自动刷新）。点击下方按钮打开 MiQroForge 平台页面完成登录
            并点击「同意」，MiQroForge 自动完成授权。
          </p>
        </div>
      </div>

      {!loggedIn ? (
        <div className="flex flex-col gap-4">
          {/* 浏览器登录（OAuth）：当前唯一的登录入口 —— 手机号/密码表单、
              环境选择与高级设置已隐藏，待后续需要时恢复。 */}
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
                : '登录 MiQroForge 账号'}
            </Button>
            <p className="text-size-2xs text-[var(--text-faint)]">
              将打开 MiQroForge 平台授权页，在页面完成登录并点击「同意」后自动回到 MiQroForge。
            </p>
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
        </div>
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
