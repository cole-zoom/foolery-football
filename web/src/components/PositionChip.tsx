import { positionColor, slotLabel } from '@/lib/positions'
import { cn } from '@/lib/cn'

/**
 * The little stamped position label. Uppercase mono, color-tinted bg,
 * tracked-out letters. Used everywhere a player is shown.
 */
export function PositionChip({
  position,
  size = 'sm',
  className,
}: {
  position: string | null | undefined
  size?: 'xs' | 'sm' | 'md'
  className?: string
}) {
  if (!position) return null
  const color = positionColor(position)
  const label = slotLabel(position)

  const sizeClasses = {
    xs: 'text-[9px] px-1 py-px h-4',
    sm: 'text-[10px] px-1.5 py-0.5 h-5',
    md: 'text-[11px] px-2 py-1 h-6',
  }

  return (
    <span
      className={cn(
        'stamp inline-flex items-center justify-center rounded-xs',
        sizeClasses[size],
        className,
      )}
      style={{
        background: `color-mix(in oklch, ${color} 18%, transparent)`,
        color: `color-mix(in oklch, ${color} 80%, white)`,
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${color} 30%, transparent)`,
      }}
    >
      {label}
    </span>
  )
}
