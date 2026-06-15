import * as Select from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Single styled dropdown used across the app. Label sits on top, control
 * below — vertically aligned, never crooked.
 *
 * For typeahead/search use Combobox instead.
 */
export function FieldSelect({
  label,
  value,
  onChange,
  options,
  className,
  triggerClassName,
  size = 'md',
  disabled,
}: {
  label?: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: ReactNode }[]
  className?: string
  triggerClassName?: string
  size?: 'sm' | 'md'
  disabled?: boolean
}) {
  const sizeCls = size === 'sm' ? 'h-8 text-[12px] px-2.5' : 'h-9 text-[13px] px-3'

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <span className="stamp text-[10px] text-ink-7 leading-none">{label}</span>
      )}
      <Select.Root value={value} onValueChange={onChange} disabled={disabled}>
        <Select.Trigger
          className={cn(
            'inline-flex items-center justify-between gap-2 rounded-md',
            'bg-ink-2 border hairline text-ink-12 cursor-pointer transition',
            'hover:bg-ink-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-signal)]/30',
            'data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed',
            sizeCls,
            triggerClassName,
          )}
        >
          <Select.Value />
          <Select.Icon asChild>
            <ChevronDown size={13} className="text-ink-7 shrink-0" />
          </Select.Icon>
        </Select.Trigger>

        <Select.Portal>
          <Select.Content
            position="popper"
            sideOffset={6}
            className={cn(
              'z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden',
              'rounded-md bg-ink-1 border hairline shadow-2xl',
              'data-[state=open]:animate-in data-[state=open]:fade-in',
            )}
          >
            <Select.Viewport className="p-1 max-h-[60vh]">
              {options.map((opt) => (
                <Select.Item
                  key={opt.value}
                  value={opt.value}
                  className={cn(
                    'relative flex items-center pl-7 pr-3 py-1.5 rounded-sm',
                    'text-[13px] text-ink-11 cursor-pointer outline-none',
                    'data-[highlighted]:bg-ink-3 data-[highlighted]:text-ink-12',
                    'data-[state=checked]:text-[var(--color-signal)]',
                  )}
                >
                  <Select.ItemIndicator className="absolute left-2 inline-flex items-center justify-center">
                    <Check size={11} strokeWidth={2.5} />
                  </Select.ItemIndicator>
                  <Select.ItemText>{opt.label}</Select.ItemText>
                </Select.Item>
              ))}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  )
}
