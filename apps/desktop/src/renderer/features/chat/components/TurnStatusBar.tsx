import { useUserInput } from '../../../contexts/UserInputContext';

/**
 * TurnStatusBar — 顶栏状态联动（v5）：有 pending 确认卡时显示
 * "等待你的确认"（accent + 脉冲点），与卡片状态联动。
 *
 * 完整 turn 状态（执行中/已取消/已停止）依赖 turn_status_changed 事件流，
 * 本期先做等待确认态；legacy 路径的 turn 事件接入后扩展。
 */
export function TurnStatusBar() {
  const { pending } = useUserInput();
  const waiting = Object.keys(pending).length > 0;
  if (!waiting) return null;

  return (
    <div
      className="inline-flex items-center gap-2 text-[12px] font-medium"
      data-testid="turn-status-waiting"
    >
      <span className="w-[7px] h-[7px] rounded-full" style={{ background: '#9db8d8' }} />
      <span style={{ color: '#6a8fb8' }}>等待你的确认</span>
    </div>
  );
}
