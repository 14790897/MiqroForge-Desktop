/**
 * 阶段 3:SURE 核查报告的结构化渲染(四块 + 12 阶段结果表)。
 *
 * 如实呈现纪律(方案 §6 假绿纪律):
 * - 总体结论用四色徽章,`not_enough_checked`(灰)与 `not_ready`(红)明显区分;
 * - `outcome`/`exit_code` 原文展示,不折算;
 * - 「尚未验证的项目」不折叠不隐瞒,逐条可见;
 * - severity/status/outcome 为自由字符串,未知值原样显示(见 shared/sureReport)。
 */

import type { ReactNode } from 'react';
import type {
  SureCheckEnvelope,
  SureFinding,
  SureLifecycle,
  SureNotChecked,
  SureRepairContract,
  SureStageRecord,
} from '../../../shared/sureReport';
import {
  aggregateLabel,
  aggregateTone,
  findingStatusLabel,
  findingTone,
  rawOutcomeLabel,
  stageOutcomeLabel,
  stageTone,
  type Tone,
} from '../../../shared/sureReport';

const BADGE_BASE =
  'inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium';

const TONE_BADGE: Record<Tone, string> = {
  ok: 'bg-[var(--success-bg)] text-[var(--success-text)]',
  warn: 'bg-[var(--warning-bg)] text-[var(--warning)]',
  danger: 'bg-[var(--danger-bg)] text-[var(--danger)]',
  info: 'bg-[var(--info-bg)] text-[var(--info)]',
  muted: 'bg-[var(--surface-muted)] text-[var(--text-muted)]',
};

function ToneBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`${BADGE_BASE} ${TONE_BADGE[tone]}`}>{children}</span>;
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-sm font-semibold text-[var(--text)] mb-2">{children}</h3>;
}

function FindingRow({ finding }: { finding: SureFinding }) {
  return (
    <div className="rounded-md border border-[var(--border)] p-3 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <ToneBadge tone={findingTone(finding.severity)}>{finding.severity}</ToneBadge>
        <span className="text-size-2xs text-[var(--text-muted)]">
          {findingStatusLabel(finding.status)}
        </span>
        {finding.is_model_only ? <ToneBadge tone="info">模型评估</ToneBadge> : null}
      </div>
      <p className="text-sm font-medium text-[var(--text)]">{finding.title}</p>
      <dl className="space-y-1 text-xs text-[var(--text-muted)]">
        <div>
          <dt className="inline text-[var(--text-faint)]">是什么:</dt>
          <dd className="inline"> {finding.what}</dd>
        </div>
        <div>
          <dt className="inline text-[var(--text-faint)]">影响:</dt>
          <dd className="inline"> {finding.impact}</dd>
        </div>
        <div>
          <dt className="inline text-[var(--text-faint)]">下一步:</dt>
          <dd className="inline"> {finding.next_action}</dd>
        </div>
      </dl>
      {finding.evidence_anchors.length > 0 ? (
        <ul className="space-y-0.5">
          {finding.evidence_anchors.map((anchor, i) => (
            <li key={i} className="text-size-2xs font-mono text-[var(--text-faint)]">
              {anchor.location} — {anchor.locator}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function NotCheckedRow({ item }: { item: SureNotChecked }) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className="mt-0.5 text-size-2xs text-[var(--text-faint)]">○</span>
      <div className="min-w-0">
        <p className="text-xs text-[var(--text)]">
          {item.title}
          {item.is_critical ? (
            <span className="ml-2">
              <ToneBadge tone="warn">关键</ToneBadge>
            </span>
          ) : null}
        </p>
        <p className="text-size-2xs text-[var(--text-muted)]">{item.reason}</p>
      </div>
    </div>
  );
}

function StageRow({ stage }: { stage: SureStageRecord }) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className="w-6 shrink-0 text-size-2xs text-[var(--text-faint)] text-right">
        {stage.number}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-[var(--text)]">{stage.title}</span>
          <ToneBadge tone={stageTone(stage.outcome)}>{stageOutcomeLabel(stage.outcome)}</ToneBadge>
        </div>
        {stage.detail ? (
          <p className="text-size-2xs text-[var(--text-muted)] mt-0.5">{stage.detail}</p>
        ) : null}
        {stage.reason_explained ? (
          <p className="text-size-2xs text-[var(--text-faint)] mt-0.5">{stage.reason_explained}</p>
        ) : null}
      </div>
    </div>
  );
}

function ContractList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <span className="text-size-2xs text-[var(--text-faint)]">{label}:</span>
      <ul className="list-disc pl-4 text-xs text-[var(--text-muted)]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function RepairContractCard({ contract }: { contract: SureRepairContract }) {
  return (
    <div className="rounded-md border border-[var(--border)] p-3 space-y-2">
      <p className="text-sm font-medium text-[var(--text)]">{contract.problem}</p>
      <p className="text-xs text-[var(--text-muted)]">{contract.why_it_matters}</p>
      <div className="space-y-1.5">
        <ContractList label="必须修复" items={contract.required_fix} />
        <ContractList label="必须保留" items={contract.preserve ?? []} />
        <ContractList label="验收标准" items={contract.acceptance} />
        <ContractList label="禁止走捷径" items={contract.forbidden_shortcuts ?? []} />
      </div>
      {contract.rechecks_that_must_pass && contract.rechecks_that_must_pass.length > 0 ? (
        <p className="text-size-2xs font-mono text-[var(--text-faint)]">
          复核项:{contract.rechecks_that_must_pass.join('、')}
        </p>
      ) : null}
    </div>
  );
}

/** 复审对比(recheck):关闭有条件,残留不隐藏。 */
function LifecycleSection({ lifecycle }: { lifecycle: SureLifecycle }) {
  return (
    <section>
      <SectionTitle>复审对比</SectionTitle>
      <p className="text-size-2xs text-[var(--text-muted)] mb-2">
        发现项只在修复契约点名的检查<strong>本次运行且全部通过</strong>时才关闭;其余原样保留。
      </p>
      {lifecycle.closed.length === 0 && lifecycle.still_open.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">没有可对比的发现项变化。</p>
      ) : (
        <div className="divide-y divide-[var(--border)]">
          {lifecycle.closed.map((f) => (
            <div key={f.id} className="flex items-center gap-2 py-1.5">
              <ToneBadge tone="ok">已关闭</ToneBadge>
              <span className="text-xs text-[var(--text)]">{f.title}</span>
            </div>
          ))}
          {lifecycle.still_open.map((f) => (
            <div key={f.id} className="flex items-center gap-2 py-1.5 flex-wrap">
              <ToneBadge tone={findingTone(f.severity)}>{f.severity}</ToneBadge>
              <span className="text-xs text-[var(--text)]">{f.title}</span>
              <span className="text-size-2xs text-[var(--text-muted)]">
                {findingStatusLabel(f.status)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function ReportView({ envelope }: { envelope: SureCheckEnvelope }) {
  const { report, stages, repairs, lifecycle } = envelope.details;
  const agg = report.aggregate;

  return (
    <div className="space-y-6">
      {/* ① 总体结论 */}
      <section>
        <div className="flex items-center gap-2 flex-wrap">
          <ToneBadge tone={aggregateTone(agg.severity)}>{aggregateLabel(agg.severity)}</ToneBadge>
          <span className="text-size-2xs font-mono text-[var(--text-faint)]">
            {rawOutcomeLabel(envelope)}
          </span>
          <span className="text-size-2xs text-[var(--text-faint)]">
            SURE {envelope.sure_version}
          </span>
        </div>
        <p className="mt-2 text-sm text-[var(--text)]">{agg.headline}</p>
        <p className="mt-1.5 text-xs text-[var(--text-muted)]">
          已核查 {report.totals.checked} · 跳过 {report.totals.skipped} · 未能运行{' '}
          {report.totals.could_not_run} · 未解决问题 {report.totals.open_findings}
        </p>
        <p className="mt-1.5 text-xs text-[var(--text-muted)]">{report.capability.summary}</p>
        {report.caveat ? (
          <p className="mt-2 border-l-2 border-[var(--border)] pl-2 text-xs text-[var(--text-muted)]">
            {report.caveat}
          </p>
        ) : null}
        {report.coverage_caveat ? (
          <p className="mt-1.5 border-l-2 border-[var(--warning)] pl-2 text-xs text-[var(--warning)]">
            {report.coverage_caveat}
          </p>
        ) : null}
      </section>

      {/* ② 复审对比(recheck) */}
      {lifecycle ? <LifecycleSection lifecycle={lifecycle} /> : null}

      {/* ③ 修复契约(repair) */}
      {repairs && repairs.length > 0 ? (
        <section>
          <SectionTitle>修复契约({repairs.length})</SectionTitle>
          <div className="space-y-2">
            {repairs.map((c) => (
              <RepairContractCard key={c.id} contract={c} />
            ))}
          </div>
        </section>
      ) : null}

      {/* ② 发现的问题 */}
      <section>
        <SectionTitle>发现的问题({report.findings.length})</SectionTitle>
        {report.findings.length === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">本次核查没有发现需要处理的问题。</p>
        ) : (
          <div className="space-y-2">
            {report.findings.map((f) => (
              <FindingRow key={f.id} finding={f} />
            ))}
          </div>
        )}
      </section>

      {/* ③ 尚未验证的项目(不折叠不隐瞒) */}
      <section>
        <SectionTitle>尚未验证的项目({report.not_checked.length})</SectionTitle>
        {report.not_checked.length === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">本次核查没有未验证项。</p>
        ) : (
          <div className="divide-y divide-[var(--border)]">
            {report.not_checked.map((item) => (
              <NotCheckedRow key={item.id} item={item} />
            ))}
          </div>
        )}
      </section>

      {/* ④ 核查阶段(12 阶段逐条;ran 只代表"执行过",不代表通过) */}
      <section>
        <SectionTitle>核查阶段</SectionTitle>
        <div className="divide-y divide-[var(--border)]">
          {stages.map((s) => (
            <StageRow key={s.number} stage={s} />
          ))}
        </div>
      </section>

      <p className="text-size-2xs font-mono text-[var(--text-faint)]">
        项目指纹 {report.project_fingerprint} · 报告 schema v{report.schema_version}
      </p>
    </div>
  );
}
