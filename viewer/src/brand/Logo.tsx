/**
 * The Ptah mark: the Djed-P knocked out of a sharp square of ink (never rounded, never red).
 * It is drawn inline so it follows the day / night tokens. The static files in public/brand (logo.svg, logo-dark.svg)
 * are the same drawing for the favicon, the pre-React loading screen and anything outside the app.
 */
export const LOGO_SRC = `${import.meta.env.BASE_URL}brand/logo.svg`
export const LOGO_DARK_SRC = `${import.meta.env.BASE_URL}brand/logo-dark.svg`

export const DJED_PATH =
  'M19.5 10H33A15.5 15.5 0 0 1 33 41H28.5V49H32.5V54H15.5V49H19.5ZM28.5 15.5H38.5A1.5 1.5 0 0 1 38.5 18.5H28.5ZM28.5 24H41.5A1.5 1.5 0 0 1 41.5 27H28.5ZM28.5 32.5H38.5A1.5 1.5 0 0 1 38.5 35.5H28.5Z'

export function Logo({ size = 32, className = '' }: { size?: number; dark?: boolean; className?: string }) {
  return (
    <svg className={'logo-img ' + className} width={size} height={size} viewBox="0 0 64 64" aria-hidden focusable="false">
      <rect width="64" height="64" fill="var(--carbon)" />
      <path transform="translate(32 32) scale(1.08) translate(-32 -32)" fill="var(--paper)" fillRule="evenodd" d={DJED_PATH} />
    </svg>
  )
}
