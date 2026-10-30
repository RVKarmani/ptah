import type { Item, Room } from './types'

const nice = (s: string) => s.replace(/_/g, ' ')

export interface LabelCtx { items: Map<string, Item>; room: Room; hasOnTop: boolean }

/** "the sofa" for an item id, "the window" for an opening, "the room" for room_center. */
function noun(id: string | undefined, ctx: LabelCtx): string {
  if (!id || id === 'room_center') return 'the room'
  const it = ctx.items.get(id)
  if (it) return 'the ' + nice(it.category).toLowerCase()
  const op = ctx.room.openings?.find((o) => o.id === id)
  if (op) return 'the ' + op.type
  const ex = ctx.room.existing?.find((o) => o.id === id)
  if (ex) return 'the ' + nice(ex.category).toLowerCase()
  return 'it'
}

const ZONE: Record<string, string> = { conversation: 'conversation area', music_corner: 'music corner', media: 'media area' }

/** Human wording for a placement constraint such as "near:sofa-1" or "surface:shelf#2". Returns null when redundant. */
export function placementLabel(raw: unknown, ctx: LabelCtx): string | null {
  let text: string
  if (typeof raw === 'string') text = raw
  else if (raw && typeof raw === 'object') {
    const o = raw as { type?: string; target?: string }
    text = `${o.type ?? ''}${o.target ? ':' + o.target : ''}`
  } else text = String(raw)
  const i = text.indexOf(':')
  const type = (i < 0 ? text : text.slice(0, i)).trim()
  const target = i < 0 ? undefined : text.slice(i + 1).trim()
  switch (type) {
    case 'edge': return 'Against the wall'
    case 'middle': return 'Away from the walls'
    case 'face': return !target || target === 'room_center' ? 'Facing into the room' : `Facing ${noun(target, ctx)}`
    case 'hint': return 'Where you wanted it'
    case 'in_front_of': return `In front of ${noun(target, ctx)}`
    case 'near': return `Close to ${noun(target, ctx)}`
    case 'far': return `Kept away from ${noun(target, ctx)}`
    case 'side_of': return `Beside ${noun(target, ctx)}`
    case 'center_align': return `Centred on ${noun(target, ctx)}`
    case 'under': return `Under ${noun(target, ctx)}`
    case 'on_top_of': return `On ${noun(target, ctx)}`
    case 'surface': {
      const m = target?.match(/^shelf#(\d+)$/)
      if (m) return `On shelf ${Number(m[1]) + 1}`
      if (ctx.hasOnTop) return null
      return target === 'seat' ? 'On the seat' : 'On the top'
    }
    case 'ceiling': return target ? `Centred above ${noun(target, ctx)}` : 'Hangs from the ceiling'
    case 'wall_mounted': return 'Hung on the wall'
    case 'attach_to': return `Mounted on ${noun(target, ctx)}`
    case 'zone': {
      const z = target ? ZONE[target] ?? nice(target) + (/(area|corner|zone|nook)$/.test(target) ? '' : ' area') : 'area'
      return `Part of the ${z}`
    }
    case 'ergo':
      switch (target) {
        case 'table_gap': return 'Comfortable legroom to the seat'
        case 'seating_face': return 'Seat faces the room'
        case 'tall_wall': return 'Tall piece kept to the wall'
        case 'window_light': return 'Keeps the window clear'
        default: return target ? nice(target).replace(/^./, (c) => c.toUpperCase()) : null
      }
    default: {
      const s = nice(text)
      return s ? s.charAt(0).toUpperCase() + s.slice(1) : null
    }
  }
}
