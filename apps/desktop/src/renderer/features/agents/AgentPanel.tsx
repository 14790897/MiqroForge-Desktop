import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { LiveAgentInfo } from '../../../shared/ipc';
import { Bot, Zap } from 'lucide-react';

export default function AgentPanel() {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<LiveAgentInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const result = await window.miqi.agents.list();
        setAgents(result?.agents || []);
      } catch (e) {
        console.error('Failed to load agents:', e);
      } finally {
        setLoading(false);
      }
    };
    load();
    const unsub = window.miqi.agents.onSpawned(() => load());
    return () => {
      unsub();
    };
  }, []);

  const statusColor = (s: string) => {
    switch (s) {
      case 'idle':
        return 'bg-[var(--text-faint)]';
      case 'thinking':
        return 'bg-[var(--warning)] animate-pulse';
      case 'executing':
        return 'bg-[var(--info)] animate-pulse';
      case 'completed':
        return 'bg-[var(--success)]';
      case 'error':
        return 'bg-[var(--danger)]';
      case 'aborted':
        return 'bg-[var(--warning)]';
      default:
        return 'bg-[var(--text-faint)]';
    }
  };

  const statusLabel = (s: string) => {
    switch (s) {
      case 'idle':
        return t('agentPanel.idle');
      case 'thinking':
        return t('agentPanel.thinking');
      case 'executing':
        return t('agentPanel.executing');
      case 'completed':
        return t('agentPanel.completed');
      case 'error':
        return t('agentPanel.error');
      case 'aborted':
        return t('agentPanel.aborted');
      default:
        return s;
    }
  };

  if (loading)
    return (
      <div className="p-4 flex items-center gap-2">
        <div className="w-4 h-4 border-2 border-[var(--border)] border-t-[var(--accent)] rounded-full animate-spin" />
        <span className="text-xs text-[var(--text-faint)]">{t('agentPanel.loading')}</span>
      </div>
    );

  return (
    <div className="p-4">
      <h2 className="text-sm font-semibold text-[var(--text)] mb-4 flex items-center gap-2">
        <Bot size={16} className="icon-mono" />
        <span className="icon-color text-base leading-none">🤖</span>
        {t('agentPanel.title')}
      </h2>
      {agents.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 px-4 rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-muted)]/30">
          <div className="w-10 h-10 rounded-full bg-[var(--surface-muted)] flex items-center justify-center mb-3 text-text-faint">
            <Zap size={18} className="icon-mono" />
            <span className="icon-color text-lg leading-none">⚡</span>
          </div>
          <p className="text-sm font-medium text-[var(--text-muted)] mb-1">
            {t('agentPanel.empty')}
          </p>
          <p className="text-xs text-[var(--text-faint)]">{t('agentPanel.emptyHint')}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {agents.map((a) => (
            <div
              key={a.agent_id}
              className="rounded-lg px-3 py-2.5 transition-colors"
              style={{
                background: 'var(--surface-muted)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${statusColor(a.status)}`} />
                <span className="text-xs font-medium text-text">{a.type}</span>
                <span className="text-size-2xs text-text-faint">{statusLabel(a.status)}</span>
              </div>
              <p className="text-xs mt-1 text-text-muted">{a.label}</p>
              <p className="text-size-2xs mt-0.5 text-text-faint">{a.agent_id}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
