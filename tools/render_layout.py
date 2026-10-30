#!/usr/bin/env python
"""Top-down render of a layout for sanity checks.

    python tools/render_layout.py samples/room.json samples/items.json samples/layout.json [-o samples/layout.png]
    python tools/render_layout.py ... --ascii        # text render to stdout (no matplotlib needed)
    python tools/render_layout.py ... --lighting samples/lighting.json --scene reading -o samples/layout_lux.png   # + lux heatmap, zones, lights

Mounted items (layout.json "mount") use other outlines: wall = dashed, ceiling = dash-dot, on_top_of = dotted, attach
(e.g. LED strip, picture light) = thick coloured line / small dashed box; each is labelled with its base height y.

Convention: x to the right, z DOWN the page (so z=0 wall is at the top); each item's front
(local +z, direction (sin r, cos r)) is drawn as an arrow; the "front" edge is the thick edge.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from ptah.layout.constraints import RoomContext  # noqa: E402
from ptah.layout.geometry import Rect  # noqa: E402


def load(args):
    room = json.load(open(args.room))
    items = {str(i["id"]): i for i in json.load(open(args.items))}
    layout = json.load(open(args.layout))
    return room, items, layout


def render_ascii(room, items, layout, cell=10.0) -> str:
    ctx = RoomContext(room)
    minx, minz, maxx, maxz = ctx.bbox
    nx, nz = int((maxx - minx) / cell) + 1, int((maxz - minz) / cell) + 1
    grid = [[" "] * nx for _ in range(nz)]
    for j in range(nz):
        for i in range(nx):
            if ctx.bd_at(minx + i * cell, minz + j * cell) >= 0:
                grid[j][i] = "."
    for o in ctx.doors:
        for j in range(nz):
            for i in range(nx):
                if o.zone.distance_to_point(minx + i * cell, minz + j * cell) <= 0 and grid[j][i] == ".":
                    grid[j][i] = "d"
    for o in ctx.windows:
        for j in range(nz):
            for i in range(nx):
                if o.thin.distance_to_point(minx + i * cell, minz + j * cell) <= cell / 2:
                    grid[j][i] = "w"
    for e in ctx.existing:
        for j in range(nz):
            for i in range(nx):
                if e.rect.distance_to_point(minx + i * cell, minz + j * cell) <= 0:
                    grid[j][i] = "#"
    legend = []
    for n, p in enumerate(layout["placements"]):
        it = items[p["item_id"]]
        r = Rect(p["x"], p["z"], it["w"], it["d"], p.get("rotation_deg", 0))
        ch = chr(ord("A") + n)
        legend.append(f"{ch} = {p['item_id']} ({r.x:.0f},{r.z:.0f}) rot {r.rot:.0f}")
        flat = it.get("h", 50) <= 2
        if p.get("mount") and p["mount"] != "floor":
            legend[-1] += f"  [{p['mount']}, y={p.get('y', 0)}]"
            continue  # mounted items are listed but not painted over the floor items
        for j in range(nz):
            for i in range(nx):
                x, z = minx + i * cell, minz + j * cell
                if r.distance_to_point(x, z) <= 0:
                    if flat:
                        if grid[j][i] in ".d":
                            grid[j][i] = ch.lower()
                    else:
                        grid[j][i] = ch
        # front marker: a '^v<>' just in front of the centre
        fx, fz = r.front
        mx, mz = r.x + fx * (it["d"] / 2 + cell * 0.6), r.z + fz * (it["d"] / 2 + cell * 0.6)
        i, j = int(round((mx - minx) / cell)), int(round((mz - minz) / cell))
        if 0 <= i < nx and 0 <= j < nz and grid[j][i] in ".d":
            grid[j][i] = {(0, 1): "v", (1, 0): ">", (0, -1): "^", (-1, 0): "<"}.get((round(fx), round(fz)), "*")
    out = ["\n".join("".join(row) for row in grid), "", "z=0 is the top row, x grows to the right; v^<> marks the item front; d=door swing, w=window, #=existing"]
    out += legend
    return "\n".join(out)


def render_png(room, items, layout, out_path, lighting=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle, Polygon

    ctx = RoomContext(room)
    fig, ax = plt.subplots(figsize=(9, 7.4))
    floor_patch = Polygon(ctx.floor, closed=True, fc="#f4efe6", ec="#333", lw=3, zorder=0)
    ax.add_patch(floor_patch)
    out_labels = []  # label anchors already used outside the walls
    if lighting:
        import numpy as np

        cols, rows = lighting["size"]
        g = lighting["grid_cm"]
        ox, oz = lighting["origin"]
        arr = np.array([[np.nan if v is None else v for v in row] for row in lighting["lux"]], dtype=float)
        im = ax.imshow(arr, extent=(ox, ox + cols * g, oz + rows * g, oz), origin="upper", cmap="YlOrRd", vmin=0,
                       vmax=max(300, min(600, float(np.nanmax(arr)))), alpha=0.6, zorder=1, interpolation="nearest")
        im.set_clip_path(floor_patch)
        cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cb.set_label(f"lux at {lighting['plane_cm']:.0f} cm ({lighting['mode']}, approximate)")
    for o in ctx.doors:
        ax.add_patch(Polygon(o.zone.corners(), closed=True, fc="#cfe8ff", ec="#4a90d9", ls="--", alpha=0.5, zorder=1))
        ax.text(o.thin.x, o.thin.z + 3, o.id, ha="center", va="top", fontsize=7, color="#2a6db0")
    for o in ctx.windows:
        ax.add_patch(Polygon(o.thin.corners(), closed=True, fc="#8fd3ff", ec="#1b75bb", lw=2, zorder=2))
        ax.add_patch(Polygon(o.zone.corners(), closed=True, fc="none", ec="#1b75bb", ls=":", alpha=0.6, zorder=2))
        ax.text(o.thin.x, o.thin.z, o.id, ha="center", va="center", fontsize=7, color="#0b4a7a")
    for e in ctx.existing:
        ax.add_patch(Polygon(e.rect.corners(), closed=True, fc="#999", ec="#444", zorder=2))
        ax.text(e.rect.x, e.rect.z, e.id, ha="center", va="center", fontsize=6)
    cmap = plt.get_cmap("tab10")
    for n, p in enumerate(layout["placements"]):
        it = items[p["item_id"]]
        r = Rect(p["x"], p["z"], it["w"], it["d"], p.get("rotation_deg", 0))
        flat = it.get("h", 50) <= 2
        c = cmap(n % 10)
        mount = p.get("mount") if p.get("mount") != "floor" else None
        y = p.get("y", 0) or 0
        if mount is None:
            ax.add_patch(Polygon(r.corners(), closed=True, fc=c, ec="k", alpha=0.35 if flat else 0.75, lw=1.2, zorder=2 if flat else 3))
            cs = r.corners()  # corners 2,3 = front edge (local +z)
            ax.plot([cs[2][0], cs[3][0]], [cs[2][1], cs[3][1]], color="k", lw=3.5, zorder=4)
            fx, fz = r.front
            ax.annotate("", xy=(r.x + fx * 30, r.z + fz * 30), xytext=(r.x, r.z),
                        arrowprops=dict(arrowstyle="->", color="k", lw=1.5), zorder=5)
            ax.text(r.x, r.z, p["item_id"], ha="center", va="center", fontsize=7, zorder=6,
                    bbox=dict(fc="white", ec="none", alpha=0.6, pad=0.5))
            continue
        strip = "strip" in str(it.get("category", "")) or (it.get("light") or {}).get("kind") == "line"
        style = {"wall": "--", "ceiling": "-.", "on_top_of": ":", "attach": "--"}.get(mount, "--")
        if strip:
            cs = r.corners()
            mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            m0, m1 = mid(cs[0], cs[3]), mid(cs[1], cs[2])
            ax.plot([m0[0], m1[0]], [m0[1], m1[1]], color="#ff9f1c", lw=4, solid_capstyle="butt", zorder=5)
        else:
            ax.add_patch(Polygon(r.corners(), closed=True, fc=c if mount != "ceiling" else "none", ec=c if mount == "ceiling" else "k",
                                 alpha=0.9 if mount != "ceiling" else 1.0, lw=1.8, ls=style, zorder=5,
                                 hatch="//" if mount == "wall" else None))
            if mount == "ceiling":
                ax.add_patch(Polygon(r.corners(), closed=True, fc=c, alpha=0.18, zorder=4))
        label = f"{p['item_id']}\ny={y:g}"
        if mount in ("wall", "attach"):
            # wall-side items are labelled outside the wall (behind the item), staggered when labels would collide
            bx, bz = -r.front[0], -r.front[1]
            k = 0
            while True:
                lx, lz = r.x + bx * (it["d"] / 2 + 26 + 24 * k), r.z + bz * (it["d"] / 2 + 26 + 24 * k)
                if all(abs(lx - ox_) + abs(lz - oz_) > 40 for ox_, oz_ in out_labels):
                    break
                k += 1
            out_labels.append((lx, lz))
            ax.annotate(label, xy=(r.x, r.z), xytext=(lx, lz), ha="center", va="center", fontsize=6, zorder=7, color="#222",
                        annotation_clip=False, arrowprops=dict(arrowstyle="-", color="#777", lw=0.6),
                        bbox=dict(fc="white", ec="#bbb", alpha=0.9, pad=0.4))
        else:
            ax.text(r.x, r.z, label, ha="center", va="center", fontsize=6, zorder=7, color="#222",
                    bbox=dict(fc="white", ec="none", alpha=0.75, pad=0.4))
    if lighting:
        zc = {"ok": "#1a9850", "dim": "#d73027", "info": "#555555"}
        for z in lighting["zones"]:
            if "center" in z:
                ax.add_patch(Circle(z["center"], z.get("radius_cm", 20) + 6, fc="none", ec=zc.get(z["status"], "k"), lw=2.5, zorder=8))
                ax.text(z["center"][0], z["center"][1] - 30, f"{z['lux']} / {z['target']} lux", ha="center", fontsize=7,
                        color=zc.get(z["status"], "k"), zorder=9, bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.3))
        lc = {"ambient": "#2166ac", "task": "#d6604d", "accent": "#762a83"}
        for l in lighting.get("lights", []):
            ax.scatter([l["pos"][0]], [l["pos"][2]], marker="*", s=130, c="gold", edgecolors=lc.get(l["layer"], "k"), linewidths=1.6, zorder=9)
    for u in layout.get("unplaced", []):
        ax.text(0.01, 0.01, f"UNPLACED: {u['item_id']} ({u['reason']})", transform=ax.transAxes, fontsize=7, color="red")
    handles = [Line2D([0], [0], color="k", ls="-", label="floor item (thick edge = front)"),
               Line2D([0], [0], color="k", ls="--", label="wall-mounted / attached (hatched)"),
               Line2D([0], [0], color="k", ls="-.", label="ceiling-hung (outline)"),
               Line2D([0], [0], color="k", ls=":", label="on top of another item"),
               Line2D([0], [0], color="#ff9f1c", lw=4, label="LED strip (no collision)"),
               Line2D([0], [0], marker="*", color="w", markerfacecolor="gold", markeredgecolor="#762a83", markersize=12, label="light emitter")]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=3, fontsize=7, frameon=False)
    minx, minz, maxx, maxz = ctx.bbox
    ax.set_xlim(minx - 60, maxx + 60)
    ax.set_ylim(maxz + 60, minz - 60)  # z down the page
    ax.set_aspect("equal")
    ax.set_title(f"{room.get('name', 'room')}  score={layout.get('score')}  (label y = base height in cm)")
    ax.set_xlabel("x (cm)")
    ax.set_ylabel("z (cm)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("room")
    ap.add_argument("items")
    ap.add_argument("layout")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--ascii", action="store_true")
    ap.add_argument("--scene", default=None, help="with --lighting: scene to show (day|evening|reading|night); default = top-level scene")
    ap.add_argument("--lighting", default=None, help="lighting.json: overlay the lux heatmap, task zones and light emitters")
    args = ap.parse_args(argv)
    room, items, layout = load(args)
    if args.ascii:
        print(render_ascii(room, items, layout))
        return 0
    out = args.output or os.path.splitext(args.layout)[0] + ".png"
    try:
        lighting = json.load(open(args.lighting)) if args.lighting else None
        if lighting and args.scene:
            sc = lighting["scenes"][args.scene]
            lighting = dict(lighting, lux=sc["lux"], zones=sc["zones"], mode=args.scene)
        render_png(room, items, layout, out, lighting)
        print(f"wrote {out}", file=sys.stderr)
    except ImportError:
        print("matplotlib not installed; ASCII render:\n", file=sys.stderr)
        print(render_ascii(room, items, layout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
