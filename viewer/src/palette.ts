const PALETTE: [RegExp, string][] = [
  [/sofa|couch|sectional|loveseat/, '#8fa08e'],
  [/arm ?chair|chair|stool|bench|ottoman|pouf/, '#c9a27e'],
  [/coffee|side_?table|table|desk/, '#b58b62'],
  [/rug|carpet/, '#d8c7a6'],
  [/lamp|light|sconce|pendant|led|strip/, '#e2bf5e'],
  [/shelf|shelv|book|storage|cabinet|dresser|sideboard|console|credenza|rack/, '#9b7c5d'],
  [/plant|tree|flower/, '#6c9f72'],
  [/tv|media|stand/, '#4a4f57'],
  [/bed|mattress/, '#9fb0c6'],
  [/mirror|art|frame|print|poster|painting/, '#c7bfd6'],
]
const FALLBACK = ['#8aa0b4', '#b99aa4', '#a4b58a', '#c7a98a', '#9a94b5']

export function categoryColor(cat: string, id: string): string {
  const c = cat.toLowerCase()
  for (const [re, col] of PALETTE) if (re.test(c)) return col
  let h = 0
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return FALLBACK[h % FALLBACK.length]
}

export const ACCENT = '#c8553d'
