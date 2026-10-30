// Download CC0 1K PBR floor textures from Poly Haven into public/textures/<preset>/{diffuse,normal,roughness}.jpg.
// Run on a machine with internet access:  npm run fetch-textures
// The viewer prefers these files over its built-in procedural textures whenever they exist.
// Slugs are best-effort: each preset lists candidates, the first one the API knows is used.
// Browse https://polyhaven.com/textures/floor (or ambientCG) and edit the lists to taste.
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = join(here, '..', 'public', 'textures')

const CANDIDATES = {
  light_oak: ['laminate_floor_02', 'wood_floor_deck', 'wood_planks'],
  mid_oak: ['wood_floor_deck', 'brown_planks_03', 'wood_planks'],
  dark_wood: ['dark_wood', 'brown_planks_03'],
  herringbone: ['herringbone_parquet', 'herringbone_parquet_02'],
  concrete: ['concrete_floor_worn_001', 'concrete_floor_02', 'concrete_wall_008'],
  tile_light: ['floor_tiles_06', 'large_square_tiles', 'marble_01'],
  tile_dark: ['slate_floor', 'slate_floor_02', 'floor_tiles_08'],
  carpet: ['fabric_pattern_07', 'carpet_02', 'fabric_pattern_05'],
}

const API = 'https://api.polyhaven.com'
const pick = (files, key) => files?.[key]?.['1k']?.jpg?.url ?? files?.[key]?.['1k']?.png?.url

async function get(url, asJson) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return asJson ? r.json() : Buffer.from(await r.arrayBuffer())
}

for (const [preset, slugs] of Object.entries(CANDIDATES)) {
  let done = false
  for (const slug of slugs) {
    try {
      const files = await get(`${API}/files/${slug}`, true)
      const d = pick(files, 'Diffuse'), n = pick(files, 'nor_gl'), r = pick(files, 'Rough')
      if (!d) continue
      const dir = join(out, preset)
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, 'diffuse.jpg'), await get(d))
      if (n) writeFileSync(join(dir, 'normal.jpg'), await get(n))
      if (r) writeFileSync(join(dir, 'roughness.jpg'), await get(r))
      console.log(`${preset}: ${slug} (CC0, polyhaven.com/a/${slug})`)
      done = true
      break
    } catch (e) {
      console.log(`  ${preset}: ${slug} failed (${e.message})`)
    }
  }
  if (!done) console.log(`${preset}: no texture downloaded, procedural fallback stays`)
}
