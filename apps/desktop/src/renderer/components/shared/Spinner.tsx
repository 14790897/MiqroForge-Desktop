import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/utils';

export interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE_CLASSES = {
  sm: 'w-4 h-4',
  md: 'w-6 h-6',
  lg: 'w-8 h-8',
} as const;

export function Spinner({ size = 'md', className }: SpinnerProps) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-label={t('spinner.loading')}
      className={cn(
        'border-2 border-[var(--border)] border-t-[var(--accent)] rounded-full animate-spin',
        SIZE_CLASSES[size],
        className
      )}
    />
  );
}
