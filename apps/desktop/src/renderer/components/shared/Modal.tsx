import { type ReactNode } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/Dialog';
import { cn } from '../../lib/utils';

interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  children: ReactNode;
  className?: string;
  hideClose?: boolean;
  /** If set, called when the user clicks outside or presses Escape.
   *  Return true to prevent the modal from closing. */
  onBeforeClose?: () => boolean;
}

/** General-purpose modal powered by Radix Dialog — use for custom-body modals. */
export function Modal({
  open,
  onOpenChange,
  title,
  children,
  className,
  hideClose,
  onBeforeClose,
}: ModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn('max-w-md', className)}
        hideClose={hideClose}
        onBeforeClose={onBeforeClose}
      >
        {title && (
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
        )}
        {children}
      </DialogContent>
    </Dialog>
  );
}
