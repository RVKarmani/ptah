import type { Item } from './types'

const KIND: Record<string, string> = { reading: 'Reading spot', ambient: 'Overall glow', desk: 'Desk', bed: 'Bedside' }

/** "reading:sofa-1" -> "Reading spot · sofa" (no internal ids in the UI). */
export function zoneLabel(name: string, items: Map<string, Item>): string {
  const [kind, ref] = name.split(':')
  const base = KIND[kind] ?? kind.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
  const it = ref ? items.get(ref) : undefined
  return it ? `${base} · ${it.category.replace(/_/g, ' ')}` : base
}
