import { useMemo } from 'react';
import { useUserInput } from '../../../contexts/UserInputContext';
import { Timeline } from './Timeline';
import { PlanCard } from './PlanCard';
import { ActionCard } from './ActionCard';
import { ConfirmCard } from './ConfirmCard';

export function isPlanCard(entry: {
  request: { goal?: string; permissions?: string[]; toolName?: string };
}): boolean {
  if (entry.request.toolName === 'ask_user_plan_confirm') return true;
  if (entry.request.toolName === 'ask_user_confirm_card') return false;
  return typeof entry.request.goal === 'string' || (entry.request.permissions?.length ?? 0) > 0;
}

export function isActionCard(entry: { request: { action?: string; target?: string } }): boolean {
  return typeof entry.request.action === 'string' && typeof entry.request.target === 'string';
}

export function isConfirmCard(entry: { request: Record<string, unknown> }): boolean {
  return !isPlanCard(entry as never) && !isActionCard(entry as never);
}

export function ConfirmCardItem({
  entry,
  resolve,
  timeoutCard,
}: {
  entry: { state: string; request: Record<string, any> };
  resolve: (
    inputId: string,
    choiceId: string,
    choiceLabel: string,
    remember?: boolean,
    rememberMode?: 'session' | 'always'
  ) => Promise<boolean> | void;
  timeoutCard: (inputId: string) => void;
}) {
  const id = entry.request.input_id as string;
  const resolvedEntry = entry.state !== 'pending';

  if (isActionCard(entry as never) && resolvedEntry) return null;

  let planPhase:
    'wait_confirm' | 'running' | 'completed' | 'cancelled' | 'wait_dangerous' | 'modified' =
    'wait_confirm';
  if (resolvedEntry) {
    if (entry.state === 'cancelled') planPhase = 'cancelled';
    else if (entry.state === 'modify') planPhase = 'modified';
    else if (entry.state === 'confirmed') {
      const statuses = Object.values(
        (entry as never as { stepsStatus?: Record<string, { status: string }> }).stepsStatus ?? {}
      );
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
              description:
                (entry.request.message as string) ||
                (entry.request.description as string | undefined),
            }}
            onResolve={(choiceId, rememberMode) =>
              resolve(
                id,
                choiceId,
                choiceId === 'confirm' ? '确认' : choiceId === 'modify' ? '修改计划' : '取消',
                // CR review（#1071）：`rememberMode !== null` 会把 deny（ActionCard
                // 不传第二参 → undefined）误判成「记住本次选择」——被拒绝的危险动作
                // 会被持久化为会话级回答，后续同类动作不再询问、直接自动拒绝。
                // 判据对齐下方 ConfirmCard 路径：只有显式选择 session/always 才记。
                rememberMode === 'session' || rememberMode === 'always',
                rememberMode ?? 'session'
              )
            }
          />
        ) : isPlanCard(entry as never) ? (
          <PlanCard
            entry={{
              title: (entry.request.title as string) ?? '',
              goal: (entry.request.goal as string) ?? '',
              steps: (
                (entry.request.steps as {
                  id?: string;
                  name?: string;
                  title?: string;
                  tools?: string[];
                }[]) ?? []
              ).map((step, index) => ({
                name: step.name ?? step.title ?? `步骤 ${index + 1}`,
                tools: Array.isArray(step.tools) ? step.tools : [],
              })),
              permissions: (entry.request.permissions as string[]) ?? [],
              phase: planPhase,
            }}
            // The second argument is intentionally the user's adjustment text.
            onResolve={(choiceId, choiceLabel) =>
              resolve(
                id,
                choiceId,
                choiceLabel ??
                  (choiceId === 'confirm'
                    ? '按当前方案执行'
                    : choiceId === 'modify'
                      ? '调整方案'
                      : '取消任务'),
                false,
                'session'
              )
            }
          />
        ) : (
          <ConfirmCard
            entry={entry as never}
            // #1071 G7 P1：**必须 return** resolve(...) 的 Promise —— 上面
            // ActionCard / PlanCard 两处是表达式箭头函数（隐式返回），此处是块体，
            // 漏 return 就等于把 Promise 吞掉，「提交失败→解锁重试」链路断在这。
            onResolve={(choiceId: string, rememberMode?: 'session' | 'always' | null) => {
              const choices =
                (entry.request.choices as { id: string; label?: string }[] | undefined) ?? [];
              const label = choices.find((choice) => choice.id === choiceId)?.label ?? choiceId;
              const remember = rememberMode === 'always' || rememberMode === 'session';
              return resolve(id, choiceId, label, remember, rememberMode ?? 'session');
            }}
            onTimeout={timeoutCard}
          />
        )}
      </div>
    </div>
  );
}

/** ConfirmCardArea is the fallback for cards not already attached to their originating assistant turn. */
export function ConfirmCardArea({
  matchedTurnIds,
  inlineCardIds,
}: {
  matchedTurnIds?: Set<string>;
  /** #1071 review P1：会被消息内联渲染的卡 id（input_id）集合——由 ChatConsole
   *  的 inlineCardsForGroup 统一推导，此处只做排除，保证同一张卡只有一个 DOM 实例。 */
  inlineCardIds?: Set<string>;
}) {
  const { pending, resolved, timelines, resolve, timeoutCard } = useUserInput();

  const allEntries = useMemo(() => {
    let merged = [...Object.values(resolved), ...Object.values(pending)];
    merged.sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
    // #1071 review P1（2026-09-16）：① 先按 id 排除「已被消息内联渲染」的卡。
    // 此前 plan/action 卡在下面的 isConfirmCard 分支里被无条件保留，于是同一张
    // 卡既在消息内（inline-cards）又在兜底区各画一份（双 DOM 实例）。
    // 未被内联的卡（无对应 assistant 消息）不在集合里，继续走下面的兜底路径，
    // 不会出现「卡消失」。
    if (inlineCardIds && inlineCardIds.size > 0) {
      merged = merged.filter((entry) => {
        const id = entry.request.input_id;
        return !id || !inlineCardIds.has(String(id));
      });
    }
    if (matchedTurnIds && matchedTurnIds.size > 0) {
      // #646-v2（CI strict 修复）：
      // ② 确认卡（ask_user_confirm_card）由工具链内联渲染——已匹配 turn 的
      //    确认卡必须从兜底排除，否则同卡双 DOM 实例（strict violation）。
      //    不带工具行的确认卡（写授权卡 / 安装授权卡）永远留在兜底区，否则
      //    两边都不画、直接消失（E2E 实测）。
      //    plan/action 卡不在此判断内：它们要么已被 ① 排除（有内联），要么
      //    留在这里渲染（无内联）——绝不再出现「已内联却仍保留」的情形。
      merged = merged.filter((entry) => {
        if (!isConfirmCard(entry as never)) return true;
        const turnId = entry.request.turn_id;
        return !turnId || !matchedTurnIds.has(turnId);
      });
    }
    return merged;
  }, [pending, resolved, matchedTurnIds, inlineCardIds]);

  if (allEntries.length === 0 && Object.keys(timelines).length === 0) return null;

  return (
    <div className="w-full flex flex-col gap-2" data-testid="confirm-card-area">
      {Object.entries(timelines).map(([turnId, timeline]) => (
        <div key={turnId} className="flex flex-col items-start w-full">
          <div className="min-w-0 w-full">
            <Timeline entry={timeline as never} />
          </div>
        </div>
      ))}
      {allEntries.map((entry) => (
        <ConfirmCardItem
          key={entry.request.input_id}
          entry={entry as never}
          resolve={resolve}
          timeoutCard={timeoutCard}
        />
      ))}
    </div>
  );
}
