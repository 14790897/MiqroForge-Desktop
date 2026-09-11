import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../lib/utils';

export interface EmptyStateProps {
  icon?: LucideIcon;
  iconSize?: number;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  iconSize = 32,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 py-12 px-4', className)}>
      {Icon && <Icon size={iconSize} className="text-[var(--text-faint)] opacity-40" />}
      <p className="text-sm text-[var(--text-muted)]">{title}</p>
      {description && <p className="text-xs text-[var(--text-faint)]">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
