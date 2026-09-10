import { useMemo } from 'react';
import { useUserInput } from '../../../contexts/UserInputContext';
import { Timeline } from './Timeline';
import { PlanCard } from './PlanCard';
import { ActionCard } from './ActionCard';
import { ConfirmCard } from './ConfirmCard';

/** #646-v2: 判定是否为任务计划卡——显式判别器优先（toolName），
 *  goal/permissions 启发式仅作 legacy 兜底（CodeRabbit）。 */
export function isPlanCard(entry: { request: { goal?: string; permissions?: string[]; toolName?: string } }): boolean {
  if (entry.request.toolName === 'ask_user_plan_confirm') return true;
  if (entry.request.toolName === 'ask_user_confirm_card') return false;
  return typeof entry.request.goal === 'string' || (entry.request.permissions?.length ?? 0) > 0;
}

/** #646-v2: 判定是否为危险动作卡（request_action_confirmation 载荷带 action/target） */
export function isActionCard(entry: { request: { action?: string; target?: string } }): boolean {
  return typeof entry.request.action === 'string' && typeof entry.request.target === 'string';
}

/** 2026-08-27：确认卡（ask_user_confirm_card 的普通卡）——由工具链行渲染
 *  （Hermes 式：审批条在工具行下），inline/兜底都跳过防重复 */
export function isConfirmCard(entry: { request: Record<string, unknown> }): boolean {
  return !isPlanCard(entry as never) && !isActionCard(entry as never);
}

/**
 * ConfirmCardItem — 单张确认/计划/危险动作卡的渲染（共享）。
 *
 * 用户 2026-08-27 裁决：计划/确认是 **AI 回答的一部分**——卡片内联在
 * 产生它的 AI 消息后面（turn_id 关联），不是消息流末尾独立区。
 * ConfirmCardArea 只兜底渲染暂未关联到消息的卡。
 */
export function ConfirmCardItem({
  entry,
  resolve,
  timeoutCard,
}: {
  entry: { state: string; request: Record<string, unknown> };
  resolve: (inputId: string, choiceId: string, choiceLabel: string, remember?: boolean, rememberMode?: 'session' | 'always') => Promise<void> | void;
  timeoutCard: (inputId: string) => void;
}) {
  const id = entry.request.input_id as string;
  const resolvedEntry = entry.state !== 'pending';

  // ActionCard：Hermes 审批条语义——确认/拒绝后审批条消失（工具行接管）
  if (isActionCard(entry as never) && resolvedEntry) return null;

  // PlanCard phase 映射：pending→等待确认；confirmed→执行中/已完成；
  // cancelled→已取消；modify→已修改（等待用户输入调整意见）
  let planPhase: 'wait_confirm' | 'running' | 'completed' | 'cancelled' | 'wait_dangerous' | 'modified' =
    'wait_confirm';
  if (resolvedEntry) {
    if (entry.state === 'cancelled') planPhase = 'cancelled';
    else if (entry.state === 'modify') planPhase = 'modified';
    else if (entry.state === 'confirmed') {
      const statuses = Object.values((entry as never as { stepsStatus?: Record<string, { status: string }> }).stepsStatus ?? {});
      planPhase =
        statuses.length > 0 && statuses.every((s) => s.status === 'success')
          ? 'completed'
          : 'running';
    }
  }

  return (
    <div className="flex flex-col items-start w-full animate-[msgIn_.35s_cubic-bezier(.22,.8,.32,1)]">
      <div className="min-w-0 w-full">
        {isActionCard(entry as never) ? (
          <ActionCard
            entry={{
              action: (entry.request.action as string) ?? 'external',
              target: (entry.request.target as string) ?? '',
              fileName: entry.request.file_name as string | undefined,
              sizeBytes: entry.request.size_bytes as number | undefined,
              sha256: entry.request.sha256 as string | undefined,
              description: (entry.request.message as string) || (entry.request.description as string | undefined),
            }}
            onResolve={(choiceId, rememberMode) =>
              resolve(
                id,
                choiceId,
                choiceId === 'confirm' ? '确认' : choiceId === 'modify' ? '修改计划' : '取消',
                rememberMode !== null,
                rememberMode ?? 'session',
              )
            }
          />
        ) : isPlanCard(entry as never) ? (
          <PlanCard
            entry={{
              title: (entry.request.title as string) ?? '',
              goal: (entry.request.goal as string) ?? '',
              steps: ((entry.request.steps as { name?: string; title?: string }[]) ?? []).map((s) => ({
                name: s.name ?? s.title ?? '',
                tools: [],
              })),
              permissions: (entry.request.permissions as string[]) ?? [],
              phase: planPhase,
            }}
            onResolve={(choiceId, rememberMode) =>
              resolve(
                id,
                choiceId,
                choiceId === 'confirm' ? '确认' : choiceId === 'modify' ? '修改计划' : '取消',
                rememberMode !== null,
                rememberMode ?? 'session',
              )
            }
          />
        ) : (
          <ConfirmCard
            entry={entry as never}
            onResolve={(choiceId: string, rememberMode?: 'session' | 'always' | null) => {
              const req = entry.request as unknown as { choices?: { id: string; label?: string }[] };
              const label = req.choices?.find((c) => c.id === choiceId)?.label ?? choiceId;
              const remember = rememberMode === 'always' || rememberMode === 'session';
              resolve(id, choiceId, label, remember, rememberMode ?? 'session');
            }}
            onTimeout={timeoutCard}
          />
        )}
      </div>
    </div>
  );
}

/**
 * ConfirmCardArea — 兜底渲染区（卡片内联到 AI 消息后，见 ConfirmCardItem）。
 *
 * 2026-08-27 用户裁决：计划/确认是 AI 回答的一部分——内联在产生它的消息
 * 后面（turn_id 关联）；这里只渲染**尚未关联到消息**的卡（turn 还在进行中
 * 时卡先出现，turn 完成后 ChatConsole 把它内联到消息后，此处自动消失）。
 */
export function ConfirmCardArea({ matchedTurnIds }: { matchedTurnIds?: Set<string> }) {
  const { pending, resolved, timelines, resolve, timeoutCard } = useUserInput();

  const allEntries = useMemo(() => {
    let merged = [...Object.values(resolved), ...Object.values(pending)];
    merged.sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
    // 已内联到 AI 消息的卡（turn_id 匹配）不再兜底渲染
    if (matchedTurnIds && matchedTurnIds.size > 0) {
      merged = merged.filter((e) => !(e.request.turn_id && matchedTurnIds.has(e.request.turn_id)));
    }
    // 2026-08-27：确认卡由工具链行渲染（Hermes 式）——兜底区只留 plan/action 卡
    return merged.filter((e) => !isConfirmCard(e as never));
  }, [pending, resolved, matchedTurnIds]);

  if (allEntries.length === 0 && Object.keys(timelines).length === 0) return null;

  return (
    <div className="w-full flex flex-col gap-2" data-testid="confirm-card-area">
      {/* #646-v2 Auto Timeline（非阻塞展示）——keyed by turnId */}
      {Object.entries(timelines).map(([turnId, tl]) => (
        <div key={turnId} className="flex flex-col items-start w-full">
          <div className="min-w-0 w-full">
            <Timeline entry={tl as never} />
          </div>
        </div>
      ))}
      {allEntries.map((entry) => (
        <ConfirmCardItem key={entry.request.input_id} entry={entry as never} resolve={resolve} timeoutCard={timeoutCard} />
      ))}
    </div>
  );
}
