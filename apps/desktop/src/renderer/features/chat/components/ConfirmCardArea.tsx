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
  ) => Promise<void> | void;
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
                rememberMode !== null,
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
            onResolve={(choiceId: string, rememberMode?: 'session' | 'always' | null) => {
              const choices =
                (entry.request.choices as { id: string; label?: string }[] | undefined) ?? [];
              const label = choices.find((choice) => choice.id === choiceId)?.label ?? choiceId;
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

/** ConfirmCardArea is the fallback for cards not already attached to their originating assistant turn. */
export function ConfirmCardArea({ matchedTurnIds }: { matchedTurnIds?: Set<string> }) {
  const { pending, resolved, timelines, resolve, timeoutCard } = useUserInput();

  const allEntries = useMemo(() => {
    let merged = [...Object.values(resolved), ...Object.values(pending)];
    merged.sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
    if (matchedTurnIds && matchedTurnIds.size > 0) {
      merged = merged.filter(
        (entry) => !(entry.request.turn_id && matchedTurnIds.has(entry.request.turn_id))
      );
    }
    return merged.filter((entry) => !isConfirmCard(entry as never));
  }, [pending, resolved, matchedTurnIds]);

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
