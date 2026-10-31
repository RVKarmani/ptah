/** Static demo build (GitHub Pages): `vite build --mode pages`. No backend, no /api calls: the sample person replays a bundled recording. */
export const PAGES = import.meta.env.MODE === 'pages'
export const REPO_URL = 'https://github.com/RVKarmani/ptah#readme'
/** What the demo's room picker offers instead of GET /api/rooms (dimensions match public/sample/room.json). */
export const PAGES_ROOMS = [{ id: 'sample-living-room', name: 'Sample living room', width_cm: 580, depth_cm: 480 }]
