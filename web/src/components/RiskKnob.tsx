import { useEffect, useState } from 'react'
import * as Slider from '@radix-ui/react-slider'
import * as Tooltip from '@radix-ui/react-tooltip'
import { cn } from '@/lib/cn'

/**
 * Risk control. Slider + editable numeric input. Both kept in sync.
 *
 * Updates `value` immediately while dragging (so the UI feels live).
 * Caller is responsible for debouncing before firing expensive work.
 */
export function RiskKnob({
  value,
  onChange,
  pending = false,
}: {
  value: number
  onChange: (v: number) => void
  /** True while a debounced query is mid-flight. Shows a subtle hint. */
  pending?: boolean
}) {
  const label = value < 0.34 ? 'SAFE' : value < 0.67 ? 'BALANCED' : 'AGGRESSIVE'
  const tooltip =
    value < 0.34
      ? 'Floor-first. Picks the player with the highest mean projection regardless of upside.'
      : value < 0.67
      ? 'Mean-projection ranking. Variance does not push the score either direction.'
      : 'Upside-first. Variance becomes a bonus — boom/bust players climb the rank.'

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3 leading-none">
        <span className="stamp text-[10px] text-ink-7">RISK</span>
        <Tooltip.Provider delayDuration={120}>
          <Tooltip.Root>
            <Tooltip.Trigger asChild>
              <span
                className={cn(
                  'stamp text-[10px] cursor-help',
                  value < 0.34 && 'text-[var(--color-good)]',
                  value >= 0.34 && value < 0.67 && 'text-ink-11',
                  value >= 0.67 && 'text-[var(--color-signal)]',
                )}
              >
                {label}
                {pending && <span className="ml-1.5 text-ink-7">·</span>}
                {pending && <span className="text-ink-7 nums">…</span>}
              </span>
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content
                side="bottom"
                sideOffset={6}
                className="max-w-xs rounded-md bg-ink-3 border hairline px-3 py-2 text-xs text-ink-10 shadow-xl z-50"
              >
                {tooltip}
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        </Tooltip.Provider>
      </div>

      <div className="flex items-center gap-3 h-9">
        <Slider.Root
          className="relative flex items-center select-none touch-none w-[180px] h-5"
          value={[value]}
          onValueChange={(v) => onChange(v[0])}
          min={0}
          max={1}
          step={0.01}
        >
          <Slider.Track className="bg-ink-3 relative grow rounded-full h-[2px]">
            <Slider.Range
              className="absolute h-full rounded-full"
              style={{
                background:
                  'linear-gradient(90deg, var(--color-good), var(--color-ink-9) 50%, var(--color-signal))',
              }}
            />
          </Slider.Track>
          <Slider.Thumb
            aria-label="Risk"
            className="block w-4 h-4 rounded-full bg-ink-12 border-2 border-ink-base shadow-md hover:scale-110 transition focus-visible:outline-2"
          />
        </Slider.Root>

        <RiskNumberInput value={value} onChange={onChange} />
      </div>
    </div>
  )
}

function RiskNumberInput({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  // Local text state so the user can type "0." or "0.5" intermediate
  // without us snapping back to a number. We only commit a numeric
  // value on blur / Enter / valid parse.
  const [text, setText] = useState(value.toFixed(2))

  useEffect(() => {
    setText(value.toFixed(2))
  }, [value])

  function commit(raw: string) {
    const n = parseFloat(raw)
    if (Number.isFinite(n)) {
      const clamped = Math.min(1, Math.max(0, n))
      onChange(clamped)
      setText(clamped.toFixed(2))
    } else {
      setText(value.toFixed(2))
    }
  }

  return (
    <input
      type="text"
      inputMode="decimal"
      aria-label="Risk value"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') {
          setText(value.toFixed(2))
          ;(e.target as HTMLInputElement).blur()
        }
      }}
      className={cn(
        'nums w-14 text-sm text-ink-12 text-right tabular-nums',
        'bg-ink-2 border hairline rounded-sm px-1.5 py-1 cursor-text',
        'focus:outline-none focus:ring-2 focus:ring-[var(--color-signal)]/30 focus:bg-ink-3 transition',
      )}
    />
  )
}
