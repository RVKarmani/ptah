// Re-copy ../samples/*.json into public/sample/ (room.json, items.json, layout.json, taste.json, ...)
import { copyFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = join(here, '..', '..', 'samples')
const dst = join(here, '..', 'public', 'sample')
if (!existsSync(src)) {
  console.error('No samples dir at', src)
  process.exit(1)
}
mkdirSync(dst, { recursive: true })
for (const f of readdirSync(src).filter((n) => n.endsWith('.json'))) {
  copyFileSync(join(src, f), join(dst, f))
  console.log('copied', f)
}
