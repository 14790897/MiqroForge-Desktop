import { useState, useEffect, useCallback } from 'react';
import { Save, Loader2, Radio, ToggleLeft, ToggleRight } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { ChannelsConfig } from '../../../shared/ipc';

// ─── Feature flags ───────────────────────────────────────────────────────────
// Toggle frontend exposure of experimental / deprecated features.
// Code modules are kept for future re-enabling or backend use.
const FEATURES = {
  /** Feishu/Lark gateway — frontend UI hidden (backend module retained) */
  FEISHU_ENABLED: false,
} as const;

// ─── Toggle row ──────────────────────────────────────────────────────────────
function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[var(--text)]">{label}</div>
        {description && (
          <div className="text-xs text-[var(--text-faint)] mt-0.5">{description}</div>
        )}
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={cn(
          'shrink-0 transition-colors',
          checked ? 'text-[var(--accent)]' : 'text-[var(--border)]'
        )}
        title={checked ? '已启用' : '已禁用'}
      >
        {checked ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
      </button>
    </div>
  );
}

// ─── Text field row ───────────────────────────────────────────────────────────
function FieldRow({
  label,
  value,
  onChange,
  placeholder,
  secret,
  description,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  secret?: boolean;
  description?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="flex flex-col gap-1.5 py-2">
      <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
        {label}
      </label>
      <div className="relative">
        <input
          type={secret && !show ? 'password' : 'text'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
          autoComplete="off"
          spellCheck={false}
        />
        {secret && (
          <button
            type="button"
            onClick={() => setShow(!show)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)]"
            tabIndex={-1}
          >
            {show ? '隐藏' : '显示'}
          </button>
        )}
      </div>
      {description && <p className="text-xs text-[var(--text-faint)]">{description}</p>}
    </div>
  );
}

// ─── Feishu section ───────────────────────────────────────────────────────────
interface FeishuSectionProps {
  config: ChannelsConfig['feishu'];
  onChange: (v: ChannelsConfig['feishu']) => void;
}

function FeishuSection({ config, onChange }: FeishuSectionProps) {
  const set = <K extends keyof ChannelsConfig['feishu']>(
    key: K,
    val: ChannelsConfig['feishu'][K]
  ) => onChange({ ...config, [key]: val });

  return (
    <div className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
      {/* Channel header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)]">
        <div className="flex items-center gap-2">
          <Radio
            size={14}
            className={config.enabled ? 'text-[var(--success)]' : 'text-[var(--border)]'}
          />
          <span className="text-sm font-medium text-[var(--text)]">Feishu / Lark</span>
          <span
            className={cn(
              'text-xs px-2 py-0.5 rounded-full',
              config.enabled
                ? 'bg-[color-mix(in_srgb,var(--success)_15%,transparent)] text-[var(--success)]'
                : 'bg-[var(--surface-muted)] text-[var(--text-faint)]'
            )}
          >
            {config.enabled ? '已启用' : '已禁用'}
          </span>
        </div>
        <button
          onClick={() => set('enabled', !config.enabled)}
          className={cn(
            'transition-colors',
            config.enabled ? 'text-[var(--accent)]' : 'text-[var(--border)]'
          )}
        >
          {config.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
        </button>
      </div>

      <div className="px-5 py-2 divide-y divide-[var(--border-subtle)]">
        <FieldRow
          label="App ID"
          value={config.app_id}
          onChange={(v) => set('app_id', v)}
          placeholder="cli_xxxxxxxxxxxxxxxx"
          description="开发者控制台 App ID"
        />
        <FieldRow
          label="App Secret"
          value={config.app_secret}
          onChange={(v) => set('app_secret', v)}
          placeholder="请输入 App Secret"
          secret
          description="开发者控制台 App Secret"
        />
        <div className="py-2 flex flex-col gap-1.5">
          <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
            允许来自{' '}
            <span className="font-normal text-[var(--text-faint)]">
              （open_id，每行一个，留空表示允许所有人）
            </span>
          </label>
          <textarea
            value={config.allow_from.join('\n')}
            onChange={(e) =>
              set(
                'allow_from',
                e.target.value
                  .split('\n')
                  .map((s) => s.trim())
                  .filter(Boolean)
              )
            }
            rows={3}
            placeholder="ou_xxxxxxxxxxxxxxxx"
            className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono resize-none"
            spellCheck={false}
          />
        </div>
        <div className="py-2 flex flex-col gap-1.5">
          <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
            回复延迟（毫秒）
          </label>
          <input
            type="number"
            min={0}
            value={config.reply_delay_ms}
            onChange={(e) => set('reply_delay_ms', Number(e.target.value))}
            className="w-40 px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] focus:outline-none focus:border-[var(--border-strong)] tabular-nums"
          />
          <p className="text-xs text-[var(--text-faint)]">消息合并窗口时间，0 表示关闭</p>
        </div>
        <div className="py-1">
          <ToggleRow
            label="群组中需要被@提及"
            description="在群聊中仅响应@提及的消息"
            checked={config.require_mention_in_groups}
            onChange={(v) => set('require_mention_in_groups', v)}
          />
        </div>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export function ChannelsPage() {
  const [config, setConfig] = useState<ChannelsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await window.miqi.channels.list();
      setConfig(result.channels);
    } catch {
      // runtime not running
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await window.miqi.channels.update(config as unknown as Record<string, unknown>);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[var(--background)]">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)]">渠道</h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">配置聊天平台集成</p>
        </div>
        <div className="flex items-center gap-2">
          {error && <span className="text-xs text-[var(--danger)]">{error}</span>}
          {saved && <span className="text-xs text-[var(--success)]">已保存</span>}
          <button
            onClick={handleSave}
            disabled={saving || !config}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-6">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-sm text-[var(--text-faint)]">
            <Loader2 size={16} className="animate-spin mr-2" /> 正在加载渠道…
          </div>
        ) : !config ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2 text-sm text-[var(--text-faint)]">
            <Radio size={24} />
            <span>MiQroForge 运行时未启动</span>
          </div>
        ) : (
          <>
            {/* Global switches */}
            <div className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl px-5 divide-y divide-[var(--border-subtle)]">
              <div className="py-2 text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                全局行为
              </div>
              <ToggleRow
                label="流式进度"
                description="将 Agent 的文本进度实时发送到渠道"
                checked={config.send_progress}
                onChange={(v) => setConfig({ ...config, send_progress: v })}
              />
              <ToggleRow
                label="发送工具提示"
                description="将工具调用信息流式发送到渠道（如 read_file(...)）"
                checked={config.send_tool_hints}
                onChange={(v) => setConfig({ ...config, send_tool_hints: v })}
              />
              <ToggleRow
                label="队列通知"
                description="向用户通知其在任务队列中的位置"
                checked={config.send_queue_notifications}
                onChange={(v) => setConfig({ ...config, send_queue_notifications: v })}
              />
            </div>

            {/* Feishu — frontend hidden, backend module retained */}
            {FEATURES.FEISHU_ENABLED && (
              <FeishuSection
                config={config.feishu}
                onChange={(feishu) => setConfig({ ...config, feishu })}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
