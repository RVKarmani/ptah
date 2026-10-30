"""Geometry primitives for the layout planner.

All lengths are centimetres on the floor plane (x right, z depth).  A rectangle is
described by its centre, its size (w along local x, d along local z) and a rotation
about the vertical axis.  Conventions (identical to three.js ``rotation.y``):

    front (local +z) = ( sin(rot),  cos(rot))      rot 0 -> faces +z, 90 -> +x
    right (local +x) = ( cos(rot), -sin(rot))
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

TOL = 0.5  # cm: touching / sub-half-centimetre penetration is tolerated

Point = Tuple[float, float]

_AL = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))  # (cos, sin) for 0/90/180/270


def norm_deg(a: float) -> float:
    a = float(a) % 360.0
    if a > 360.0 - 1e-9:
        a = 0.0
    return a


class Rect:
    """Oriented rectangle on the floor plane."""

    __slots__ = ("x", "z", "w", "d", "rot", "cs", "sn", "hx", "hz", "aligned")

    def __init__(self, x: float, z: float, w: float, d: float, rot: float = 0.0):
        self.x = float(x)
        self.z = float(z)
        self.w = float(w)
        self.d = float(d)
        rot = norm_deg(rot)
        k = round(rot / 90.0)
        if abs(rot - k * 90.0) < 1e-6:
            k %= 4
            rot = k * 90.0
            self.cs, self.sn = _AL[k]
            self.aligned = True
            if k % 2 == 0:
                self.hx, self.hz = self.w / 2.0, self.d / 2.0
            else:
                self.hx, self.hz = self.d / 2.0, self.w / 2.0
        else:
            r = math.radians(rot)
            self.cs, self.sn = math.cos(r), math.sin(r)
            self.aligned = False
            self.hx = abs(self.cs) * self.w / 2.0 + abs(self.sn) * self.d / 2.0
            self.hz = abs(self.sn) * self.w / 2.0 + abs(self.cs) * self.d / 2.0
        self.rot = rot

    # ---- frame -------------------------------------------------------
    @property
    def front(self) -> Point:
        return (self.sn, self.cs)

    @property
    def right(self) -> Point:
        return (self.cs, -self.sn)

    def to_local(self, px: float, pz: float) -> Point:
        dx, dz = px - self.x, pz - self.z
        return (dx * self.cs - dz * self.sn, dx * self.sn + dz * self.cs)

    def corners(self) -> List[Point]:
        hw, hd = self.w / 2.0, self.d / 2.0
        out = []
        for lx, lz in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
            out.append((self.x + lx * self.cs + lz * self.sn, self.z - lx * self.sn + lz * self.cs))
        return out

    def aabb(self) -> Tuple[float, float, float, float]:
        return (self.x - self.hx, self.z - self.hz, self.x + self.hx, self.z + self.hz)

    @property
    def area(self) -> float:
        return self.w * self.d

    def half_extent_along(self, nx: float, nz: float) -> float:
        """Half-extent of the rectangle projected on the unit vector (nx, nz)."""
        return self.w / 2.0 * abs(self.cs * nx - self.sn * nz) + self.d / 2.0 * abs(self.sn * nx + self.cs * nz)

    def distance_to_point(self, px: float, pz: float) -> float:
        """Euclidean distance from a point to the rectangle (0 when inside)."""
        if self.aligned:
            ex = abs(px - self.x) - self.hx
            ez = abs(pz - self.z) - self.hz
        else:
            lx, lz = self.to_local(px, pz)
            ex = abs(lx) - self.w / 2.0
            ez = abs(lz) - self.d / 2.0
        if ex < 0.0:
            ex = 0.0
        if ez < 0.0:
            ez = 0.0
        return math.hypot(ex, ez)

    def contains_strict(self, px: float, pz: float, tol: float = TOL) -> bool:
        lx, lz = self.to_local(px, pz)
        return abs(lx) < self.w / 2.0 - tol and abs(lz) < self.d / 2.0 - tol

    def as_dict(self) -> dict:
        return {"x": self.x, "z": self.z, "w": self.w, "d": self.d, "rotation_deg": self.rot}

    def __repr__(self) -> str:  # pragma: no cover
        return f"Rect(x={self.x:.1f}, z={self.z:.1f}, w={self.w:.1f}, d={self.d:.1f}, rot={self.rot:.0f})"


# ----------------------------------------------------------------------------
# Rectangle / rectangle
# ----------------------------------------------------------------------------

def rects_overlap(a: Rect, b: Rect, tol: float = TOL) -> bool:
    """True when the rectangles interpenetrate by more than ``tol`` cm."""
    dx, dz = b.x - a.x, b.z - a.z
    if abs(dx) >= a.hx + b.hx - tol or abs(dz) >= a.hz + b.hz - tol:
        return False
    if a.aligned and b.aligned:
        return True
    for nx, nz in ((a.cs, -a.sn), (a.sn, a.cs), (b.cs, -b.sn), (b.sn, b.cs)):
        if abs(dx * nx + dz * nz) >= a.half_extent_along(nx, nz) + b.half_extent_along(nx, nz) - tol:
            return False
    return True


def seg_point_dist(px: float, pz: float, ax: float, az: float, bx: float, bz: float) -> float:
    vx, vz = bx - ax, bz - az
    L2 = vx * vx + vz * vz
    if L2 < 1e-12:
        return math.hypot(px - ax, pz - az)
    t = ((px - ax) * vx + (pz - az) * vz) / L2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * vx), pz - (az + t * vz))


def _segs_cross(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 * d2 != 0 and d3 * d4 != 0


def seg_seg_dist(p1: Point, p2: Point, p3: Point, p4: Point) -> float:
    if _segs_cross(p1, p2, p3, p4):
        return 0.0
    return min(
        seg_point_dist(p1[0], p1[1], p3[0], p3[1], p4[0], p4[1]),
        seg_point_dist(p2[0], p2[1], p3[0], p3[1], p4[0], p4[1]),
        seg_point_dist(p3[0], p3[1], p1[0], p1[1], p2[0], p2[1]),
        seg_point_dist(p4[0], p4[1], p1[0], p1[1], p2[0], p2[1]),
    )


def rect_gap(a: Rect, b: Rect) -> float:
    """Edge-to-edge distance between two rectangles (0 if touching/overlapping)."""
    if a.aligned and b.aligned:
        ex = max(0.0, abs(b.x - a.x) - (a.hx + b.hx))
        ez = max(0.0, abs(b.z - a.z) - (a.hz + b.hz))
        return math.hypot(ex, ez)
    if rects_overlap(a, b, 0.0):
        return 0.0
    ca, cb = a.corners(), b.corners()
    best = 1e18
    for i in range(4):
        for j in range(4):
            d = seg_seg_dist(ca[i], ca[(i + 1) % 4], cb[j], cb[(j + 1) % 4])
            if d < best:
                best = d
    return best


def convex_intersection_area(a: Rect, b: Rect) -> float:
    """Area of the intersection of two rectangles (Sutherland-Hodgman clipping)."""
    if abs(b.x - a.x) >= a.hx + b.hx or abs(b.z - a.z) >= a.hz + b.hz:
        return 0.0
    if a.aligned and b.aligned:
        ox = min(a.x + a.hx, b.x + b.hx) - max(a.x - a.hx, b.x - b.hx)
        oz = min(a.z + a.hz, b.z + b.hz) - max(a.z - a.hz, b.z - b.hz)
        return max(0.0, ox) * max(0.0, oz)
    poly = a.corners()
    clip = b.corners()
    n = len(clip)
    for i in range(n):
        p, q = clip[i], clip[(i + 1) % n]
        inp, poly = poly, []
        if not inp:
            break

        def inside(pt):
            return (q[0] - p[0]) * (pt[1] - p[1]) - (q[1] - p[1]) * (pt[0] - p[0]) >= -1e-9

        def inter(s, e):
            x1, y1, x2, y2 = s[0], s[1], e[0], e[1]
            x3, y3, x4, y4 = p[0], p[1], q[0], q[1]
            den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(den) < 1e-12:
                return e
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
            return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

        s = inp[-1]
        for e in inp:
            if inside(e):
                if not inside(s):
                    poly.append(inter(s, e))
                poly.append(e)
            elif inside(s):
                poly.append(inter(s, e))
            s = e
    if len(poly) < 3:
        return 0.0
    area = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


# ----------------------------------------------------------------------------
# Polygons
# ----------------------------------------------------------------------------

def signed_area(poly: Sequence[Point]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def polygon_centroid(poly: Sequence[Point]) -> Point:
    a = signed_area(poly)
    n = len(poly)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    cx = cz = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cr
        cz += (y1 + y2) * cr
    return (cx / (6.0 * a), cz / (6.0 * a))


def point_in_polygon_strict(px: float, pz: float, poly: Sequence[Point]) -> bool:
    """Ray casting; boundary points may go either way."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if (zi > pz) != (zj > pz):
            if px < (xj - xi) * (pz - zi) / (zj - zi) + xi:
                inside = not inside
        j = i
    return inside


def dist_to_boundary(px: float, pz: float, poly: Sequence[Point]) -> float:
    n = len(poly)
    best = 1e18
    for i in range(n):
        ax, az = poly[i]
        bx, bz = poly[(i + 1) % n]
        d = seg_point_dist(px, pz, ax, az, bx, bz)
        if d < best:
            best = d
    return best


def point_in_polygon(px: float, pz: float, poly: Sequence[Point], tol: float = TOL) -> bool:
    if point_in_polygon_strict(px, pz, poly):
        return True
    return dist_to_boundary(px, pz, poly) <= tol


def polygon_bbox(poly: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    zs = [p[1] for p in poly]
    return (min(xs), min(zs), max(xs), max(zs))


def rect_in_polygon(rect: Rect, poly: Sequence[Point], tol: float = TOL, bbox=None) -> bool:
    """True when the rectangle lies inside the (simple) polygon, within ``tol``."""
    if bbox is None:
        bbox = polygon_bbox(poly)
    ax0, az0, ax1, az1 = rect.aabb()
    if ax0 < bbox[0] - tol or az0 < bbox[1] - tol or ax1 > bbox[2] + tol or az1 > bbox[3] + tol:
        return False
    corners = rect.corners()
    for cx, cz in corners:
        if not point_in_polygon(cx, cz, poly, tol):
            return False
    n = len(poly)
    for i in range(n):
        vx, vz = poly[i]
        if ax0 < vx < ax1 and az0 < vz < az1 and rect.contains_strict(vx, vz, tol):
            return False
    # proper crossings between polygon edges and rectangle edges
    for i in range(n):
        p1, p2 = poly[i], poly[(i + 1) % n]
        if max(p1[0], p2[0]) <= ax0 + tol or min(p1[0], p2[0]) >= ax1 - tol:
            continue
        if max(p1[1], p2[1]) <= az0 + tol or min(p1[1], p2[1]) >= az1 - tol:
            continue
        for j in range(4):
            q1, q2 = corners[j], corners[(j + 1) % 4]
            if _crosses_with_tol(p1, p2, q1, q2, tol):
                return False
    return True


def _crosses_with_tol(p1: Point, p2: Point, q1: Point, q2: Point, tol: float) -> bool:
    """Segments p and q cross properly, with both q endpoints more than tol off line p."""
    def signed(a, b, c):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-12:
            return 0.0
        return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / L

    s1, s2 = signed(p1, p2, q1), signed(p1, p2, q2)
    if not ((s1 > tol and s2 < -tol) or (s1 < -tol and s2 > tol)):
        return False
    t1, t2 = signed(q1, q2, p1), signed(q1, q2, p2)
    return (t1 > 1e-9 and t2 < -1e-9) or (t1 < -1e-9 and t2 > 1e-9)


# ----------------------------------------------------------------------------
# Walls
# ----------------------------------------------------------------------------

@dataclass
class WallFrame:
    id: str
    sx: float
    sz: float
    ex: float
    ez: float
    length: float
    tx: float  # unit tangent start -> end
    tz: float
    nx: float  # unit inward normal
    nz: float
    rot: float  # rotation_deg of an item whose back is flush to this wall (front = inward normal)

    def point(self, s: float, depth: float = 0.0) -> Point:
        """Point ``s`` cm along the wall from its start, ``depth`` cm into the room."""
        return (self.sx + self.tx * s + self.nx * depth, self.sz + self.tz * s + self.nz * depth)

    def zone(self, offset: float, width: float, depth: float) -> Rect:
        """Rectangle covering [offset, offset+width] along the wall and ``depth`` into the room."""
        cx, cz = self.point(offset + width / 2.0, depth / 2.0)
        return Rect(cx, cz, width, depth, self.rot)

    def depth_of(self, px: float, pz: float) -> float:
        return (px - self.sx) * self.nx + (pz - self.sz) * self.nz

    def along(self, px: float, pz: float) -> float:
        return (px - self.sx) * self.tx + (pz - self.sz) * self.tz


def build_wall_frames(floor: Sequence[Point], walls: Optional[Sequence[dict]]) -> List[WallFrame]:
    ccw = signed_area(floor) > 0
    segs = []
    if walls:
        for w in walls:
            segs.append((str(w["id"]), tuple(w["start"]), tuple(w["end"])))
    else:
        n = len(floor)
        for i in range(n):
            segs.append((f"w{i + 1}", tuple(floor[i]), tuple(floor[(i + 1) % n])))
    frames = []
    for wid, (sx, sz), (ex, ez) in [(a, b, c) for a, b, c in segs]:
        L = math.hypot(ex - sx, ez - sz)
        if L < 1e-9:
            continue
        tx, tz = (ex - sx) / L, (ez - sz) / L
        # left normal (-tz, tx) points inward for a counter-clockwise polygon
        nx, nz = (-tz, tx) if ccw else (tz, -tx)
        rot = norm_deg(math.degrees(math.atan2(nx, nz)))
        frames.append(WallFrame(wid, sx, sz, ex, ez, L, tx, tz, nx, nz, rot))
    return frames
