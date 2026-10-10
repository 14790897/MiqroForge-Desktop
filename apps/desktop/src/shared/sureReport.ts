/**
 * 阶段 3:SURE 报告的前端类型与展示映射。
 *
 * 类型是后端 miqi/runtime/sure_report.py(Pydantic 镜像)的 TS 镜像;
 * 展示映射遵循"如实保真"原则——severity/status/outcome 是**自由字符串**
 * (CLI 以展示形态输出,如 "Must fix"/"Cannot confirm"),映射函数容忍
 * 展示形态与 snake_case 两种写法,未知值一律回退 muted + 原样显示,
 * 绝不猜测、绝不美化。
 */

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'muted';

export interface SureAggregate {
  severity: string;
  headline: string;
  is_green: boolean;
}

export interface SureCapability {
  tier: number;
  summary: string;
  blind_spots: string[];
}

export interface SureEvidenceAnchor {
  location: string;
  locator: string;
  subject: string;
}

export interface SureFinding {
  id: string;
  title: string;
  what: string;
  impact: string;
  severity: string;
  status: string;
  next_action: string;
  is_model_only: boolean;
  evidence_anchors: SureEvidenceAnchor[];
}

export interface SureNotChecked {
  id: string;
  title: string;
  reason: string;
  is_critical: boolean;
}

export interface SureTotals {
  checked: number;
  skipped: number;
  could_not_run: number;
  open_findings: number;
}

export interface SureReport {
  schema_version: number;
  project_fingerprint: string;
  aggregate: SureAggregate;
  ready_for_hand_off: boolean;
  must_caveat_requirements: boolean;
  capability: SureCapability;
  findings: SureFinding[];
  not_checked: SureNotChecked[];
  totals: SureTotals;
  caveat?: string | null;
  coverage_caveat?: string | null;
  [k: string]: unknown;
}

export interface SureStageRecord {
  number: number;
  stage: string;
  title: string;
  outcome: string;
  reason?: string | null;
  reason_explained?: string | null;
  detail?: string | null;
}

export interface SureCheckDetails {
  project: string;
  purpose: string;
  mode: string;
  state: string;
  report: SureReport;
  stages: SureStageRecord[];
  [k: string]: unknown;
}

export interface SureCheckEnvelope {
  command: string;
  details: SureCheckDetails;
  exit_code: number;
  outcome: string;
  protocol_version: number;
  sure_version: string;
  [k: string]: unknown;
}

function norm(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
}

/** 总体结论四色:not_enough_checked 必须与 not_ready 明显区分(灰 vs 红)。 */
export function aggregateTone(severity: string): Tone {
  switch (norm(severity)) {
    case 'green':
      return 'ok';
    case 'needs_attention':
      return 'warn';
    case 'not_ready':
      return 'danger';
    case 'not_enough_checked':
      return 'muted';
    default:
      return 'muted';
  }
}

const AGGREGATE_LABELS: Record<string, string> = {
  green: '通过',
  needs_attention: '需要关注',
  not_ready: '尚未就绪',
  not_enough_checked: '检查不足,无法判断',
};

/** 未知值原样显示(不翻译、不美化)。 */
export function aggregateLabel(severity: string): string {
  return AGGREGATE_LABELS[norm(severity)] ?? severity;
}

export function findingTone(severity: string): Tone {
  switch (norm(severity)) {
    case 'must_fix':
      return 'danger';
    case 'should_fix_first':
      return 'warn';
    case 'can_fix_later':
      return 'info';
    case 'note':
      return 'muted';
    default:
      return 'muted';
  }
}

const FINDING_STATUS_LABELS: Record<string, string> = {
  open: '未解决',
  resolved: '已解决',
  accepted_risk: '已知悉,接受风险',
  cannot_confirm: '无法确认',
};

/** 未知值原样显示。 */
export function findingStatusLabel(status: string): string {
  return FINDING_STATUS_LABELS[norm(status)] ?? status;
}

/** 阶段结果:ran=已执行(info,不代表通过)/ not_run=未运行(warn)/ 其余原样。 */
export function stageTone(outcome: string): Tone {
  switch (norm(outcome)) {
    case 'ran':
      return 'info';
    case 'not_run':
      return 'warn';
    case 'not_part_of_work':
      return 'muted';
    default:
      return 'muted';
  }
}

const STAGE_OUTCOME_LABELS: Record<string, string> = {
  ran: '已执行',
  not_run: '未运行',
  not_part_of_work: '与本次核查无关',
};

/** 未知值原样显示。 */
export function stageOutcomeLabel(outcome: string): string {
  return STAGE_OUTCOME_LABELS[norm(outcome)] ?? outcome;
}

/** 总体结论的 raw 徽章文案(如实呈现 outcome/exit_code,不折算)。 */
export function rawOutcomeLabel(
  envelope: Pick<SureCheckEnvelope, 'outcome' | 'exit_code'>
): string {
  return `${envelope.outcome} · exit ${envelope.exit_code}`;
}

// ── IPC 契约(阶段 3,与 preload/main 共用)────────────────────────────────

/** `sure.health` 的结果:二进制是否可用(不可用 → 页面引导安装)。 */
export interface SureHealth {
  installed: boolean;
  binary: string | null;
  version: string | null;
  error: string | null;
}

/** 运行中进度心跳(静默期也发,防 bridge 600s 空闲 drain)。 */
export interface SureCheckProgress {
  taskId: string;
  project: string;
  elapsedMs: number;
  state: string;
}

/** `sure_check_report` 事件载荷:信封包在 `envelope` 字段里(含任务关联信息)。 */
export interface SureCheckReportEvent {
  taskId: string;
  project: string;
  envelope: SureCheckEnvelope;
  elapsedMs: number;
}

/** 核查失败(含"报告版本不支持"等,code 供 UI 归类,message 面向用户)。 */
export interface SureCheckFailure {
  taskId: string;
  project: string;
  message: string;
  code: string;
  /** 进程中退出时的 stderr/stdout 尾巴(诊断用,可能缺省)。 */
  stderrTail?: string;
}

/** 用户取消(进程树已终止,无报告产出——与真实 CLI 一致)。 */
export interface SureCheckCancelled {
  taskId: string;
  project: string;
  elapsedMs: number;
}

/** `sure.check.start` 的即时结果(数据以事件为准)。 */
export interface SureCheckStartResult {
  taskId: string;
  project: string;
}

/** `sure.check.status`:页面挂载/重开时的对账快照。 */
export interface SureCheckStatus {
  task: {
    taskId: string;
    project: string;
    state: string;
    elapsedMs: number;
    startedAt: number;
  } | null;
}

/** 主进程 sendSafeWithError 的统一封装(可展示错误码,如 SURE_UNAVAILABLE/SURE_BUSY)。 */
export type SureApiResult<T> = { ok: true; value: T } | { ok: false; error: string; code?: string };

/** 运行时长显示(面板心跳用):m:ss,超过 1 小时显示 h:mm:ss。 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const ss = String(s).padStart(2, '0');
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${ss}`;
  return `${m}:${ss}`;
}
