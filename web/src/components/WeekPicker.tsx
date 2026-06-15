import { Combobox } from './Combobox'
import { FieldSelect } from './FieldSelect'

const WEEK_OPTIONS = Array.from({ length: 18 }).map((_, i) => ({
  value: String(i + 1),
  label: `Week ${i + 1}`,
}))

export function WeekPicker({
  value,
  onChange,
}: {
  value: number
  onChange: (n: number) => void
}) {
  return (
    <FieldSelect
      label="WEEK"
      value={String(value)}
      onChange={(v) => onChange(parseInt(v, 10))}
      options={WEEK_OPTIONS}
      triggerClassName="min-w-[110px]"
    />
  )
}

export function SeasonPicker({
  value,
  onChange,
  options,
}: {
  value: number
  onChange: (n: number) => void
  options: number[]
}) {
  return (
    <Combobox
      label="SEASON"
      value={String(value)}
      onChange={(v) => onChange(parseInt(v, 10))}
      options={options.map((s) => ({ value: String(s), label: String(s) }))}
      placeholder="Search season…"
      className="min-w-[120px]"
    />
  )
}
