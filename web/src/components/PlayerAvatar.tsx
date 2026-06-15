import { useState } from 'react'
import { cn } from '@/lib/cn'
import { positionColor } from '@/lib/positions'
import type { Player } from '@/lib/api'

/**
 * Headshot anchor — ESPN-style small circular crop with position-tinted
 * fallback chip if the Sleeper CDN doesn't have the player.
 *
 * Relies entirely on browser HTTP cache for the headshot CDN. The
 * player_id is immutable, so cache hit-rate is near 100% after first
 * load.
 */
export function PlayerAvatar({
  player,
  size = 44,
  className,
}: {
  player: Player | null | undefined
  size?: number
  className?: string
}) {
  const [failed, setFailed] = useState(false)

  if (!player) {
    return (
      <div
        className={cn(
          'shrink-0 rounded-full bg-ink-3 border hairline grid place-items-center',
          className,
        )}
        style={{ width: size, height: size }}
      >
        <span className="stamp text-[10px] text-ink-7">—</span>
      </div>
    )
  }

  const initials = (player.full_name ?? '?')
    .split(' ')
    .map((p) => p[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

  const color = positionColor(player.position)

  if (failed || !player.headshot_url) {
    return (
      <div
        className={cn(
          'shrink-0 rounded-full grid place-items-center font-mono text-[11px] font-semibold border hairline',
          className,
        )}
        style={{
          width: size,
          height: size,
          background: `color-mix(in oklch, ${color} 22%, var(--color-ink-3))`,
          color: `color-mix(in oklch, ${color} 80%, white)`,
          boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${color} 35%, transparent)`,
        }}
        aria-label={player.full_name ?? 'unknown player'}
      >
        {initials}
      </div>
    )
  }

  return (
    <div
      className={cn(
        'shrink-0 rounded-full overflow-hidden bg-ink-3 border hairline relative',
        className,
      )}
      style={{
        width: size,
        height: size,
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${color} 30%, transparent)`,
      }}
    >
      <img
        src={player.headshot_url}
        alt={player.full_name ?? ''}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
        className="w-full h-full object-cover"
        style={{ background: 'var(--color-ink-3)' }}
      />
    </div>
  )
}
