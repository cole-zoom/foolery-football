import { useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const KEY = 'pb-theme'

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY) as Theme | null
    if (saved === 'dark' || saved === 'light') return saved
  } catch { /* ignore */ }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains('dark') ? 'dark' : 'light',
  )

  useEffect(() => {
    const t = readTheme()
    setTheme(t)
  }, [])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    document.documentElement.classList.toggle('dark', next === 'dark')
    try { localStorage.setItem(KEY, next) } catch { /* ignore */ }
  }

  return { theme, toggle }
}
