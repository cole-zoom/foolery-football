import { cn } from '@/lib/cn'

/**
 * One big tabular number with a small mono label underneath.
 * The signature ESPN-style stat readout — right-aligned numbers feel
 * "scoreboard"; the label feels "press-box terminal".
 */
export function StatNumber({
  value,
  label,
  decimals = 2,
  size = 'md',
  tone = 'default',
  align = 'left',
  className,
}: {
  value: number | string
  label: string
  decimals?: number
  size?: 'sm' | 'md' | 'lg' | 'xl'
  tone?: 'default' | 'muted' | 'signal' | 'good' | 'warn'
  align?: 'left' | 'right'
  className?: string
}) {
  const formatted =
    typeof value === 'number' ? value.toFixed(decimals) : value

  const sizeCls = {
    sm: 'text-base',
    md: 'text-xl',
    lg: 'text-3xl',
    xl: 'text-5xl',
  }[size]

  const toneCls = {
    default: 'text-ink-12',
    muted:   'text-ink-9',
    signal:  'text-[var(--color-signal)]',
    good:    'text-[var(--color-conf-high)]',
    warn:    'text-[var(--color-conf-medium)]',
  }[tone]

  return (
    <div className={cn('flex flex-col', align === 'right' && 'items-end', className)}>
      <span className={cn('nums font-semibold leading-none', sizeCls, toneCls)}>
        {formatted}
      </span>
      <span className="stamp text-[10px] text-ink-7 mt-1.5">{label}</span>
    </div>
  )
}
