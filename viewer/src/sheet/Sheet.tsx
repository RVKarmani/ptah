import type { ReactNode } from 'react'
import { Logo } from '../brand/Logo'
import { Icon } from '../ui'
import { useTheme } from '../theme'

const COLS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const ROWS = ['1', '2', '3', '4', '5', '6']

/** Reference letters and numbers around the frame, like the margin of a drawing sheet. Decorative. */
function Refs() {
  return (
    <>
      <div className="refs t" aria-hidden>{COLS.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="refs b" aria-hidden>{COLS.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="refs l" aria-hidden>{ROWS.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="refs r" aria-hidden>{ROWS.map((c) => <span key={c}>{c}</span>)}</div>
    </>
  )
}

/**
 * One sheet of the drawing set: outer hairline, the margin references, the 2 px inner frame, the body and a title block.
 * `onTop` puts the title block at the head on narrow screens (the room view, whose bottom belongs to the panel).
 */
export function Sheet({ title, tb, children, className = '', onTop = false, bodyClass = '' }: {
  title?: string; tb: ReactNode; children: ReactNode; className?: string; onTop?: boolean; bodyClass?: string
}) {
  return (
    <div className={'sheet' + (className ? ' ' + className : '') + (onTop ? ' tb-top' : '')}>
      <Refs />
      <main className="inner" aria-label={title}>
        <section className={'body ' + bodyClass}>{children}</section>
        <footer className="tb">{tb}</footer>
      </main>
    </div>
  )
}

/** The mark cell at the left end of every title block. */
export function TbMark({ onClick, label = 'Ptah' }: { onClick?: () => void; label?: string }) {
  return onClick
    ? <button type="button" className="tb-mark" onClick={onClick} aria-label={label} title={label}><Logo size={56} /></button>
    : <div className="tb-mark"><Logo size={56} /></div>
}

/** A labelled cell of the title block. `fill` takes the free space. */
export function Cell({ label, children, fill = false, className = '', rubric = false }: { label: string; children: ReactNode; fill?: boolean; className?: string; rubric?: boolean }) {
  return (
    <div className={'tb-cell' + (fill ? ' fill' : '') + (className ? ' ' + className : '')}>
      <small>{label}</small>
      <b className={rubric ? 'rub' : undefined}>{children}</b>
    </div>
  )
}

/** Day / night switch, a cell of its own so it sits in the same place on every sheet. */
export function ThemeCell() {
  const [theme, toggle] = useTheme()
  const next = theme === 'night' ? 'day' : 'night'
  return (
    <div className="tb-cell tb-theme">
      <button type="button" className="tb-link" onClick={toggle} aria-label={`Switch to the ${next} sheet`} title={`Switch to the ${next} sheet`}>
        <Icon name={theme === 'night' ? 'sun' : 'moon'} size={20} />
      </button>
    </div>
  )
}

/** A dimension line with 45 degree end ticks and the figure set in a gap. */
export function Dim({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={'dim ' + className} aria-hidden>
      <u className="a" /><i>{children}</i><u className="b" />
    </div>
  )
}
