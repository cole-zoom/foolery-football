import { useEffect, useState } from 'react'

/**
 * Returns `value` after `delayMs` of no further changes.
 * Used to gate the risk slider so the API only fires after the user
 * stops moving the knob.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}
