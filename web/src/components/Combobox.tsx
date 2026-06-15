import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Command } from 'cmdk'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/cn'

/**
 * Typeable dropdown. Like FieldSelect but with a search input.
 * Used for the season picker (and any future "many options" case).
 */
export function Combobox({
  label,
  value,
  onChange,
  options,
  placeholder = 'Search…',
  className,
}: {
  label?: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const current = options.find((o) => o.value === value)

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <span className="stamp text-[10px] text-ink-7 leading-none">{label}</span>
      )}
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger
          className={cn(
            'inline-flex items-center justify-between gap-2 h-9 px-3 rounded-md',
            'bg-ink-2 border hairline text-ink-12 text-[13px] cursor-pointer transition',
            'hover:bg-ink-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-signal)]/30',
          )}
        >
          <span>{current?.label ?? value}</span>
          <ChevronDown size={13} className="text-ink-7 shrink-0" />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            sideOffset={6}
            align="start"
            className="z-50 w-[var(--radix-popover-trigger-width)] min-w-[180px] rounded-md bg-ink-1 border hairline shadow-2xl overflow-hidden"
          >
            <Command className="flex flex-col" loop>
              <div className="border-b hairline">
                <Command.Input
                  placeholder={placeholder}
                  className="w-full bg-transparent px-3 py-2.5 text-[13px] text-ink-12 placeholder:text-ink-7 outline-none"
                />
              </div>
              <Command.List className="max-h-60 overflow-y-auto p-1">
                <Command.Empty className="px-3 py-3 text-[12px] text-ink-7">
                  No matches.
                </Command.Empty>
                {options.map((opt) => (
                  <Command.Item
                    key={opt.value}
                    value={opt.label}
                    onSelect={() => {
                      onChange(opt.value)
                      setOpen(false)
                    }}
                    className={cn(
                      'relative flex items-center pl-7 pr-3 py-1.5 rounded-sm',
                      'text-[13px] text-ink-11 cursor-pointer outline-none',
                      'data-[selected=true]:bg-ink-3 data-[selected=true]:text-ink-12',
                    )}
                  >
                    {opt.value === value && (
                      <Check size={11} strokeWidth={2.5} className="absolute left-2" />
                    )}
                    {opt.label}
                  </Command.Item>
                ))}
              </Command.List>
            </Command>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  )
}
