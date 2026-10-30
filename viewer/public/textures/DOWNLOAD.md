# Optional CC0 PBR floor textures

The viewer ships with generated (procedural) floor textures for every preset, so nothing here is required.
The sandbox this viewer was built in had no access to Poly Haven or ambientCG, so no files were downloaded.
To upgrade the floors to real photographed PBR materials, put 1K maps in this folder:

    public/textures/<preset>/diffuse.jpg      (sRGB albedo / colour)
    public/textures/<preset>/normal.jpg       (OpenGL / "nor_gl" normal map)
    public/textures/<preset>/roughness.jpg    (optional)

`.png` and `.webp` work too. When `diffuse.*` exists the viewer uses the files, otherwise the procedural texture.
Textures tile at real-world scale (cm per tile): wood 120, herringbone 60, concrete 200, tile 120, carpet 80
(see `FLOORS` in `src/floors.ts`).

## Fastest way

    cd viewer && npm run fetch-textures

(`scripts/fetch-textures.mjs` asks the Poly Haven API for the 1K Diffuse / nor_gl / Rough maps and tries a few candidate
slugs per preset; slugs are best-effort, edit the list if one is not found.)

## Suggested CC0 assets (Poly Haven, https://polyhaven.com/textures, CC0)

| preset | asset (verify the slug on the site) | direct maps (1K) |
| --- | --- | --- |
| light_oak | `laminate_floor_02` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/laminate_floor_02/laminate_floor_02_diff_1k.jpg |
| mid_oak | `wood_floor_deck` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/wood_floor_deck/wood_floor_deck_diff_1k.jpg |
| dark_wood | `dark_wood` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/dark_wood/dark_wood_diff_1k.jpg |
| herringbone | `herringbone_parquet` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/herringbone_parquet/herringbone_parquet_diff_1k.jpg |
| concrete | `concrete_floor_worn_001` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/concrete_floor_worn_001/concrete_floor_worn_001_diff_1k.jpg |
| tile_light | `floor_tiles_06` | https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/floor_tiles_06/floor_tiles_06_diff_1k.jpg |
| tile_dark | a dark slate / tile asset (e.g. `slate_floor`) | same URL pattern |
| carpet | a fabric / carpet asset (e.g. `fabric_pattern_07`) | same URL pattern |

Same URL pattern for the other maps: `<slug>_nor_gl_1k.jpg` (normal, save as `normal.jpg`) and `<slug>_rough_1k.jpg` (save as `roughness.jpg`).

ambientCG (https://ambientcg.com, CC0) works as well: download the 1K-JPG zip of e.g. a `WoodFloor*`, `Concrete*`,
`Tiles*` or `Carpet*` asset and copy `*_Color.jpg` -> `diffuse.jpg`, `*_NormalGL.jpg` -> `normal.jpg`, `*_Roughness.jpg` -> `roughness.jpg`.
