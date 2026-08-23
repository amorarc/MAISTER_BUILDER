#!/usr/bin/env python3
"""
ldr_collision_checker.py

A CLI tool that parses an LDraw (.ldr / .mpd) file — the format LeoCAD
models are saved in — and reports:

  1. COLLISIONS  — pairs of parts whose bounding boxes overlap in 3D space,
                    reported with the exact source line number of each part.
  2. ISOLATED PARTS — parts that have no other part within a plausible
                    connection distance (a heuristic proxy for "floating /
                    disconnected from the model").

MPD / SUBMODEL HANDLING
-----------------------
The model is FLATTENED before checking: starting from the main model (the
first "0 FILE" block), every reference to another block inside the same
document is expanded recursively, accumulating the transformation matrix,
so that every real part ends up in a single world-space coordinate system.
This means collisions BETWEEN submodels are found, and a submodel reference
is never mistaken for a missing part.

Blocks whose name ends in ".dat" are treated as *part definitions* embedded
in the MPD (the standard way OMR models ship unofficial parts) — their
geometry is read in place instead of being expanded as a submodel.

Use --per-block to get the old behaviour: each "0 FILE" block checked in
isolation, with no expansion.

Two accuracy modes:

  --library <path-to-ldraw-folder>
        Full mode. Recursively resolves each referenced part (and any
        sub-parts / primitives it references) inside the official LDraw
        parts library (the folder that contains "parts/", "p/", "models/"
        etc. — e.g. what LDCad, LeoCAD or the LDraw.org "complete" download
        installs). Computes a REAL geometric bounding box per part by
        walking its vertex data. This gives meaningfully accurate results.

  (no --library given)
        Fallback mode. Every part is approximated with a generic brick-sized
        box (20 x 24 x 20 LDU — a 1x1 brick footprint/height). This still
        finds *gross* overlaps (two parts placed on the exact same spot,
        wildly wrong translations, etc.) but will under- and over-report
        compared to full mode. A warning banner is printed when this mode
        is used.

IMPORTANT LIMITATION (inherent to any bounding-box approach, not just this
script): legitimately connected pieces overlap on purpose — a stud sits
inside an anti-stud tube, a plate's underside overlaps the top of the plate
below it, clips overlap bars, a door sits inside its frame, etc. This script
shrinks each box slightly (--shrink, default 0.80) to cut down on this noise,
but you should expect false positives on real, valid connections. Judge a
model against a known-good baseline rather than against zero.

USAGE
-----
    python3 ldr_collision_checker.py model.ldr
    python3 ldr_collision_checker.py model.mpd --library /opt/ldraw
    python3 ldr_collision_checker.py model.mpd --library /opt/ldraw --shrink 0.7
    python3 ldr_collision_checker.py model.ldr --json report.json

EXIT CODE
---------
    0  - no collisions and no isolated parts found
    1  - collisions and/or isolated parts found
    2  - fatal error (bad file, etc.)
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def norm_name(name):
    """
    Canonical key for a part / submodel name.

    LDraw's canonical path separator is the backslash ("s\\3001s01.dat",
    "48\\1-8cyli.dat") regardless of host OS. Normalising it to "/" makes
    os.path.join work on POSIX and keeps block lookups consistent.
    """
    return name.strip().replace("\\", "/").lower()


IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


# --------------------------------------------------------------------------
# LDraw parsing
# --------------------------------------------------------------------------

@dataclass
class PartInstance:
    line_no: int
    color: str
    x: float
    y: float
    z: float
    matrix: list  # 3x3 rotation/scale matrix, row-major, 9 floats (a..i)
    part_name: str
    submodel: str  # which FILE block (for .mpd) this instance lives in


@dataclass
class FlatInstance:
    """A leaf part placed in world space after submodel expansion."""
    src: PartInstance          # the original type-1 line
    matrix: list               # accumulated world 3x3
    pos: tuple                 # accumulated world translation
    path: tuple                # chain of submodels it was reached through


@dataclass
class ParsedModel:
    # block key (norm_name of the "0 FILE" value) -> list of PartInstance
    blocks: dict = field(default_factory=dict)
    # block key -> raw text lines of that block (used to read embedded parts)
    block_lines: dict = field(default_factory=dict)
    main_submodel: str = None

    @property
    def instances(self):
        """Every type-1 line in the document, in source order."""
        out = []
        for insts in self.blocks.values():
            out.extend(insts)
        out.sort(key=lambda i: i.line_no)
        return out


def parse_ldr_file(path):
    """
    Parses an .ldr or .mpd file into per-block PartInstance lists plus the
    raw lines of each block. For plain .ldr files (no "0 FILE" directives)
    everything belongs to a single implicit block.
    """
    with open(path, "r", errors="replace") as f:
        raw_lines = f.readlines()

    model = ParsedModel()
    current = "__main__"
    model.main_submodel = current
    model.blocks[current] = []
    model.block_lines[current] = []
    seen_file_directive = False

    for idx, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line:
            continue

        upper = line.upper()

        # "0 FILE <name>" starts a new block (MPD convention)
        if upper.startswith("0 FILE "):
            current = norm_name(line[7:])
            if not seen_file_directive:
                model.main_submodel = current
            seen_file_directive = True
            model.blocks.setdefault(current, [])
            model.block_lines.setdefault(current, [])
            continue

        # "0 NOFILE" ends the current block; "0 !DATA" starts a base64 blob
        if upper == "0 NOFILE" or upper.startswith("0 !DATA "):
            current = None
            continue

        if current is None:
            continue

        model.block_lines[current].append(line)

        tokens = line.split()
        if not tokens:
            continue

        if tokens[0] == "1" and len(tokens) >= 14:
            try:
                color = tokens[1]
                x, y, z = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
                matrix = [float(t) for t in tokens[5:14]]
                part_name = " ".join(tokens[14:]).strip()
            except ValueError:
                # malformed line, skip but don't crash the whole run
                continue

            model.blocks[current].append(
                PartInstance(
                    line_no=idx,
                    color=color,
                    x=x, y=y, z=z,
                    matrix=matrix,
                    part_name=part_name,
                    submodel=current,
                )
            )

    # drop the implicit block if the file turned out to be a proper MPD
    if seen_file_directive and not model.blocks.get("__main__"):
        model.blocks.pop("__main__", None)
        model.block_lines.pop("__main__", None)

    return model


# --------------------------------------------------------------------------
# Linear algebra
# --------------------------------------------------------------------------

def mat_vec_mul(m, v):
    a, b, c, d, e, f, g, h, i = m
    x, y, z = v
    return (a * x + b * y + c * z,
            d * x + e * y + f * z,
            g * x + h * y + i * z)


def mat_mul(m, n):
    """Row-major 3x3 product m * n."""
    out = [0.0] * 9
    for r in range(3):
        for c in range(3):
            out[r * 3 + c] = (m[r * 3 + 0] * n[0 * 3 + c] +
                              m[r * 3 + 1] * n[1 * 3 + c] +
                              m[r * 3 + 2] * n[2 * 3 + c])
    return out


# --------------------------------------------------------------------------
# Model flattening (submodel expansion)
# --------------------------------------------------------------------------

def flatten_model(model):
    """
    Expands the main model's submodel references recursively into a flat list
    of FlatInstance in a single world coordinate system.

    A reference is expanded as a submodel when it names a block defined in
    this document AND does not end in ".dat" (a ".dat" block is an embedded
    part definition, which stays a leaf). Returns (flat, cycles).
    """
    flat = []
    cycles = []

    def walk(block_key, mat, pos, path, stack):
        for inst in model.blocks.get(block_key, []):
            wmat = mat_mul(mat, inst.matrix)
            local = mat_vec_mul(mat, (inst.x, inst.y, inst.z))
            wpos = (local[0] + pos[0], local[1] + pos[1], local[2] + pos[2])

            key = norm_name(inst.part_name)
            is_submodel = key in model.blocks and not key.endswith(".dat")

            if is_submodel:
                if key in stack:
                    cycles.append((inst, path + (inst.part_name,)))
                    continue
                walk(key, wmat, wpos, path + (inst.part_name,), stack | {key})
            else:
                flat.append(FlatInstance(src=inst, matrix=wmat, pos=wpos,
                                         path=path))

    main = model.main_submodel
    walk(main, IDENTITY, (0.0, 0.0, 0.0), (), {main})
    return flat, cycles


def unflattened(model):
    """Old behaviour: every instance stays in its own block's coordinates."""
    flat = []
    for inst in model.instances:
        flat.append(FlatInstance(src=inst, matrix=list(inst.matrix),
                                 pos=(inst.x, inst.y, inst.z),
                                 path=(inst.submodel,)))
    return flat, []


# --------------------------------------------------------------------------
# Part geometry resolution (full / library mode)
# --------------------------------------------------------------------------

LIBRARY_SUBDIRS = ["parts", os.path.join("parts", "s"), "p",
                    os.path.join("p", "48"), os.path.join("p", "8")]


def find_part_file(part_name, library_root):
    """Search the common LDraw library subfolders for a part/primitive file."""
    name = norm_name(part_name)
    candidates = [name]
    if not name.endswith(".dat"):
        candidates.append(name + ".dat")

    for sub in LIBRARY_SUBDIRS:
        base = os.path.join(library_root, sub)
        if not os.path.isdir(base):
            continue
        for cand in candidates:
            direct = os.path.join(base, cand)
            if os.path.isfile(direct):
                return direct
            # case-insensitive fallback (LDraw refs are sometimes mixed case).
            # Split so that refs carrying a subdirectory still work.
            cand_dir, cand_file = os.path.split(cand)
            search_dir = os.path.join(base, cand_dir) if cand_dir else base
            try:
                for fname in os.listdir(search_dir):
                    if fname.lower() == cand_file:
                        return os.path.join(search_dir, fname)
            except OSError:
                pass
    return None


def get_part_lines(part_name, library_root, model=None):
    """
    Returns the text lines defining a part, preferring a definition embedded
    in the MPD document over the on-disk library. None if unresolvable.
    """
    key = norm_name(part_name)
    if model is not None and key in model.block_lines:
        return model.block_lines[key]
    if not library_root:
        return None
    path = find_part_file(part_name, library_root)
    if path is None:
        return None
    try:
        with open(path, "r", errors="replace") as f:
            return f.readlines()
    except OSError:
        return None


# Above this many cached vertices a part degrades to its 8 bbox corners. Real
# LDraw parts stay far below it; the cap only bounds pathological shortcuts.
MAX_CACHED_POINTS = 60000


def compute_part_points(part_name, library_root, cache, stack=None, model=None):
    """
    Recursively collects a part's vertices in its own LOCAL frame.

    Vertices — not bounding-box corners — must be carried up the reference
    tree. Re-bounding a child's AABB after an off-axis rotation inflates it by
    up to 41%, which is how 11477 (a curved slope whose cylinder is rotated 45
    degrees) ends up 0.49 LDU oversized in four directions and lands off the
    stud grid.

    Returns a list of (x, y, z), or None if the part cannot be resolved.
    """
    key = norm_name(part_name)
    if key in cache:
        return cache[key]
    if stack is None:
        stack = set()
    if key in stack:
        return None  # guard against circular refs
    stack.add(key)

    lines = get_part_lines(part_name, library_root, model)
    if lines is None:
        cache[key] = None
        stack.discard(key)
        return None

    points = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        t = tokens[0]

        if t == "1" and len(tokens) >= 14:
            try:
                sx, sy, sz = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
                smat = [float(v) for v in tokens[5:14]]
                sub_name = " ".join(tokens[14:]).strip()
            except ValueError:
                continue
            sub = compute_part_points(sub_name, library_root, cache, stack, model)
            if not sub:
                continue
            for (cx, cy, cz) in sub:
                rx, ry, rz = mat_vec_mul(smat, (cx, cy, cz))
                points.append((rx + sx, ry + sy, rz + sz))

        elif t in ("2", "3", "4", "5"):
            # line / triangle / quad / optional-line: raw vertex coords follow
            # (skip color token at index 1).
            # A type-5 optional line has 4 points, but points 3 and 4 are
            # control points that steer visibility — they are not geometry and
            # can sit far outside the part, so only points 1-2 count.
            coords = tokens[2:8] if t == "5" else tokens[2:]
            vals = []
            for c in coords:
                try:
                    vals.append(float(c))
                except ValueError:
                    vals.append(None)
            for i in range(0, len(vals) - 2, 3):
                trio = vals[i:i + 3]
                if None in trio:
                    continue
                points.append((trio[0], trio[1], trio[2]))

    stack.discard(key)

    if not points:
        cache[key] = None
        return None

    if len(points) > MAX_CACHED_POINTS:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        zs = [p[2] for p in points]
        points = [(x, y, z)
                  for x in (min(xs), max(xs))
                  for y in (min(ys), max(ys))
                  for z in (min(zs), max(zs))]

    cache[key] = points
    return points


def compute_part_bbox(part_name, library_root, cache, stack=None, model=None):
    """
    LOCAL (untransformed) bounding box of a part.
    Returns ((minx,miny,minz),(maxx,maxy,maxz)) or None if unresolvable.
    """
    pts = compute_part_points(part_name, library_root, cache, stack, model)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


# --------------------------------------------------------------------------
# Fallback (no-library) generic bbox
# --------------------------------------------------------------------------

# Generic 1x1 brick footprint/height in LDU (20 LDU stud spacing, 24 LDU brick
# height), centered on the origin horizontally, sitting on top of the
# reference point vertically (LDraw parts are typically authored with their
# origin at the bottom-center / stud-grid reference point).
GENERIC_BBOX = ((-10.0, -24.0, -10.0), (10.0, 0.0, 10.0))

GENERIC_POINTS = [(x, y, z)
                  for x in (-10.0, 10.0)
                  for y in (-24.0, 0.0)
                  for z in (-10.0, 10.0)]


# --------------------------------------------------------------------------
# World-space AABB + collision / isolation logic
# --------------------------------------------------------------------------

def world_aabb(instance, local_points, shrink):
    """
    World-space AABB of a placed part.

    The part's actual vertices are transformed and then bounded — bounding
    first and rotating the 8 corners afterwards would inflate a 45-degree
    placement by up to 41%.
    """
    m, (tx, ty, tz) = instance.matrix, instance.pos
    a, b, c, d, e, f, g, h, i = m

    minx = miny = minz = float("inf")
    maxx = maxy = maxz = float("-inf")
    for (px, py, pz) in local_points:
        wx = a * px + b * py + c * pz + tx
        wy = d * px + e * py + f * pz + ty
        wz = g * px + h * py + i * pz + tz
        if wx < minx: minx = wx
        if wx > maxx: maxx = wx
        if wy < miny: miny = wy
        if wy > maxy: maxy = wy
        if wz < minz: minz = wz
        if wz > maxz: maxz = wz

    # shrink around the box center to reduce false positives from intentional
    # interlocking connections
    if shrink != 1.0:
        cx, cy, cz = ((minx + maxx) / 2, (miny + maxy) / 2, (minz + maxz) / 2)
        minx = cx + (minx - cx) * shrink
        maxx = cx + (maxx - cx) * shrink
        miny = cy + (miny - cy) * shrink
        maxy = cy + (maxy - cy) * shrink
        minz = cz + (minz - cz) * shrink
        maxz = cz + (maxz - cz) * shrink

    return (minx, miny, minz), (maxx, maxy, maxz)


def aabb_overlap(a, b):
    (aminx, aminy, aminz), (amaxx, amaxy, amaxz) = a
    (bminx, bminy, bminz), (bmaxx, bmaxy, bmaxz) = b
    if amaxx <= bminx or bmaxx <= aminx:
        return None
    if amaxy <= bminy or bmaxy <= aminy:
        return None
    if amaxz <= bminz or bmaxz <= aminz:
        return None
    overlap = (min(amaxx, bmaxx) - max(aminx, bminx),
               min(amaxy, bmaxy) - max(aminy, bminy),
               min(amaxz, bmaxz) - max(aminz, bminz))
    return overlap


def find_collisions(world_boxes, same_space_only):
    """
    Sweep-and-prune along X, then exact AABB test. `world_boxes` is a list of
    (FlatInstance, aabb).
    """
    order = sorted(range(len(world_boxes)), key=lambda i: world_boxes[i][1][0][0])
    collisions = []
    for pi, i in enumerate(order):
        inst_a, box_a = world_boxes[i]
        a_maxx = box_a[1][0]
        for j in order[pi + 1:]:
            inst_b, box_b = world_boxes[j]
            if box_b[0][0] >= a_maxx:
                break  # no later box can start before a ends
            if same_space_only and inst_a.src.submodel != inst_b.src.submodel:
                continue
            overlap = aabb_overlap(box_a, box_b)
            if overlap is not None:
                collisions.append((inst_a, inst_b, overlap))
    return collisions


def find_isolated(world_boxes, threshold, same_space_only):
    """
    Center-to-center nearest-neighbour heuristic, accelerated with a uniform
    grid sized to the threshold. Falls back to a full scan only for parts with
    no neighbour inside the search radius (so the reported distance is exact).
    """
    centers = []
    for inst, (mn, mx) in world_boxes:
        centers.append((inst, ((mn[0] + mx[0]) / 2,
                               (mn[1] + mx[1]) / 2,
                               (mn[2] + mx[2]) / 2)))

    cell = max(threshold, 1.0)
    grid = {}
    for idx, (_, c) in enumerate(centers):
        k = (int(c[0] // cell), int(c[1] // cell), int(c[2] // cell))
        grid.setdefault(k, []).append(idx)

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

    isolated = []
    for i, (inst_a, ca) in enumerate(centers):
        base = (int(ca[0] // cell), int(ca[1] // cell), int(ca[2] // cell))
        nearest = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                        if j == i:
                            continue
                        inst_b, cb = centers[j]
                        if same_space_only and inst_a.src.submodel != inst_b.src.submodel:
                            continue
                        d = dist(ca, cb)
                        if nearest is None or d < nearest:
                            nearest = d
        if nearest is not None and nearest <= threshold:
            continue
        # nothing close by — do an exact full scan to report the true distance
        nearest = None
        for j, (inst_b, cb) in enumerate(centers):
            if j == i:
                continue
            if same_space_only and inst_a.src.submodel != inst_b.src.submodel:
                continue
            d = dist(ca, cb)
            if nearest is None or d < nearest:
                nearest = d
        if nearest is None or nearest > threshold:
            isolated.append((inst_a, nearest))
    return isolated


def check_model(model, library_root, shrink, isolation_threshold,
                per_block=False):
    cache = {}
    world_boxes = []
    unresolved = []

    flat, cycles = unflattened(model) if per_block else flatten_model(model)

    for inst in flat:
        local = None
        if library_root or not per_block:
            local = compute_part_points(inst.src.part_name, library_root,
                                        cache, model=model)
        if not local:
            unresolved.append(inst)
            local = GENERIC_POINTS
        world_boxes.append((inst, world_aabb(inst, local, shrink)))

    collisions = find_collisions(world_boxes, same_space_only=per_block)
    isolated = find_isolated(world_boxes, isolation_threshold,
                             same_space_only=per_block)

    return collisions, isolated, unresolved, flat, cycles


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def fmt_part(inst):
    where = " < ".join(reversed(inst.path)) if inst.path else inst.src.submodel
    loc = f" [in {where}]" if where and where != "__main__" else ""
    return (f"line {inst.src.line_no}: {inst.src.part_name} @ "
            f"({inst.pos[0]:.1f}, {inst.pos[1]:.1f}, {inst.pos[2]:.1f}){loc}")


def print_report(source_path, collisions, isolated, unresolved, cycles,
                 flat, library_root, shrink, per_block):
    print(f"LDraw collision/connectivity report for: {source_path}")
    print(f"  mode: {'library (' + library_root + ')' if library_root else 'fallback generic-bbox'}")
    print(f"  expansion: {'per-block (no submodel expansion)' if per_block else 'flattened to world space'}")
    print(f"  parts checked: {len(flat)}")
    print(f"  shrink factor: {shrink}")
    print("-" * 70)

    if not library_root:
        print("WARNING: no --library given. Using a generic brick-size bounding")
        print("box for every part. Results are a rough approximation only.")
        print("-" * 70)

    if cycles:
        print(f"ERROR: {len(cycles)} circular submodel reference(s):")
        for inst, path in cycles[:10]:
            print(f"    - line {inst.line_no}: {' < '.join(reversed(path))}")
        print("-" * 70)

    if unresolved:
        names = sorted(set(i.src.part_name for i in unresolved))
        print(f"NOTE: {len(unresolved)} part instance(s) could not be resolved in the")
        print("library and fell back to the generic bbox. Unresolved part files:")
        for nm in names[:20]:
            print(f"    - {nm}")
        if len(names) > 20:
            print(f"    ... and {len(names) - 20} more")
        print("-" * 70)

    if collisions:
        print(f"COLLISIONS FOUND: {len(collisions)}")
        for inst_a, inst_b, overlap in collisions:
            print(f"  * {fmt_part(inst_a)}")
            print(f"    collides with")
            print(f"    {fmt_part(inst_b)}")
            print(f"    overlap (LDU, x/y/z): "
                  f"{overlap[0]:.1f} / {overlap[1]:.1f} / {overlap[2]:.1f}")
            print()
    else:
        print("COLLISIONS FOUND: 0")

    print("-" * 70)

    if isolated:
        print(f"POSSIBLY DISCONNECTED PARTS: {len(isolated)}")
        for inst, dist in isolated:
            dist_str = f"{dist:.1f} LDU to nearest part" if dist is not None else "no other parts in model"
            print(f"  * {fmt_part(inst)}  ({dist_str})")
    else:
        print("POSSIBLY DISCONNECTED PARTS: 0")

    print("-" * 70)
    print("Note: isolation is a distance heuristic (center-to-center vs. "
          "--isolation-threshold), not true stud/clip connectivity — a part "
          "can be close in space but not actually snapped/connected, or "
          "legitimately connected at a distance greater than the threshold "
          "for long parts (technic beams, hoses, etc.).")


def write_json_report(path, collisions, isolated, unresolved, cycles, flat,
                      library_root, shrink, per_block):
    def ref(inst):
        return {"line": inst.src.line_no,
                "part": inst.src.part_name,
                "position": list(inst.pos),
                "submodel_path": list(inst.path)}

    data = {
        "mode": "library" if library_root else "fallback",
        "expansion": "per_block" if per_block else "flattened",
        "library_root": library_root,
        "shrink": shrink,
        "parts_checked": len(flat),
        "collisions": [
            {"part_a": ref(a), "part_b": ref(b), "overlap_ldu": list(overlap)}
            for a, b, overlap in collisions
        ],
        "possibly_disconnected": [
            dict(ref(inst), nearest_part_distance_ldu=dist)
            for inst, dist in isolated
        ],
        "circular_references": [
            {"line": inst.line_no, "path": list(path)} for inst, path in cycles
        ],
        "unresolved_part_count": len(unresolved),
        "unresolved_parts": sorted(set(i.src.part_name for i in unresolved)),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Detect part collisions and possibly-disconnected parts "
                    "in an LDraw (.ldr/.mpd) model, e.g. one built in LeoCAD.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("model", help="Path to the .ldr or .mpd file to check")
    ap.add_argument("--library", "-l", default=None,
                     help="Path to an LDraw parts library root (folder containing "
                          "'parts/' and 'p/'). Enables accurate geometry-based "
                          "bounding boxes instead of the generic fallback box.")
    ap.add_argument("--shrink", type=float, default=0.80,
                     help="Shrink factor (0-1) applied to each part's bounding "
                          "box before collision testing, to reduce false "
                          "positives from legitimate interlocking connections. "
                          "Default: 0.80")
    ap.add_argument("--isolation-threshold", type=float, default=40.0,
                     help="Max center-to-center distance (LDU) for a part to be "
                          "considered 'connected enough' to its nearest "
                          "neighbor. Parts farther than this from every other "
                          "part are flagged as possibly disconnected. "
                          "Default: 40.0 (LDU; 1 brick ~= 20 LDU wide)")
    ap.add_argument("--per-block", action="store_true",
                     help="Do not expand submodels; check each '0 FILE' block "
                          "in its own coordinate system (legacy behaviour).")
    ap.add_argument("--json", default=None,
                     help="Also write a machine-readable JSON report to this path")
    args = ap.parse_args()

    if not os.path.isfile(args.model):
        print(f"ERROR: file not found: {args.model}", file=sys.stderr)
        sys.exit(2)

    if args.library and not os.path.isdir(args.library):
        print(f"ERROR: --library path not found or not a directory: {args.library}",
              file=sys.stderr)
        sys.exit(2)

    if not (0.0 < args.shrink <= 1.0):
        print("ERROR: --shrink must be between 0 (exclusive) and 1 (inclusive)",
              file=sys.stderr)
        sys.exit(2)

    try:
        model = parse_ldr_file(args.model)
    except OSError as e:
        print(f"ERROR: could not read {args.model}: {e}", file=sys.stderr)
        sys.exit(2)

    if not model.instances:
        print("No part instances (type-1 lines) found in this file — nothing to check.")
        sys.exit(0)

    collisions, isolated, unresolved, flat, cycles = check_model(
        model, args.library, args.shrink, args.isolation_threshold,
        per_block=args.per_block
    )

    print_report(args.model, collisions, isolated, unresolved, cycles, flat,
                 args.library, args.shrink, args.per_block)

    if args.json:
        write_json_report(args.json, collisions, isolated, unresolved, cycles,
                          flat, args.library, args.shrink, args.per_block)
        print(f"\nJSON report written to: {args.json}")

    sys.exit(1 if (collisions or isolated) else 0)


if __name__ == "__main__":
    main()
