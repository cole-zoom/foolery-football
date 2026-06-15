import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Right-side drawer used for the recommendation panel and player detail.
 * Wraps Radix Dialog for a11y; styled to look like a press-box clipboard
 * sliding in from the right.
 */
export function Sheet({
  open,
  onOpenChange,
  title,
  subtitle,
  children,
  width = 'lg',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  width?: 'md' | 'lg' | 'xl'
}) {
  const widthCls = {
    md: 'w-[440px]',
    lg: 'w-[560px]',
    xl: 'w-[760px]',
  }[width]

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/55 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in" />
        <Dialog.Content
          className={cn(
            'fixed right-0 top-0 h-screen bg-ink-1 border-l hairline shadow-2xl',
            'flex flex-col z-50',
            widthCls,
            'max-w-[100vw]',
            'data-[state=open]:animate-in data-[state=open]:slide-in-from-right',
            'data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right',
            'duration-300',
          )}
        >
          <div className="flex items-start justify-between px-7 pt-6 pb-5 border-b hairline">
            <div>
              {subtitle && <div className="stamp text-[10px] text-ink-7 mb-1">{subtitle}</div>}
              {title && (
                <Dialog.Title className="display text-2xl text-ink-12">
                  {title}
                </Dialog.Title>
              )}
            </div>
            <Dialog.Close
              className="rounded-md p-1.5 text-ink-8 hover:text-ink-12 hover:bg-ink-3 transition"
              aria-label="Close"
            >
              <X size={16} />
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
