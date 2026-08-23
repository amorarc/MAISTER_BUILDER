"""Build a catalogue of the unique LDraw parts (.dat) used by the OMR corpus.

For every distinct part referenced by the models in ``data/ldraw_omr_sets/`` this
script records:

* ``part_id``    - the .dat name without extension (``3024``)
* the *definition* taken from the part's own header on
  https://library.ldraw.org - description, category, keywords, author, official
  or unofficial status
* the *bounding box*, computed by expanding the part's whole subfile tree
  (subparts ``parts/s/`` and primitives ``p/``, ``p/48/``, ``p/8/``) and
  transforming every vertex - this is what tells you how tall a piece is and
  therefore at which height the next one goes
* the *sets that reference it*, by the ``set_id`` used in
  ``data/ldraw_omr_sets/metadata.csv``

All the pieces are kept in ``data/lego_pieces/``. The folder is seeded once from
the official ``complete.zip`` (~145 MB, every official part, subpart and
primitive) because crawling 7000+ parts one file at a time earns an HTTP 429
from library.ldraw.org within the first minute. Anything the archive lacks is
then fetched individually, and later runs need no network at all.

Two files are written (long/wide split, so the per-set data stays queryable):

    data/parts/parts_catalog.csv    one row per part, dimensions + definition
    data/parts/part_set_usage.csv   one row per (part, set), with a quantity

Geometry notes - LDraw's Y axis points DOWN and a part's origin sits on its top
face, so the body runs from y=0 down to ``max_y`` while the studs stick UP into
negative y (``min_y`` is about -4 on a standard part).

That makes ``height_y`` - the raw extent - the wrong number to stack with: it
counts the stud, which sinks into the tube of whatever goes above. Use
``body_height_y`` (= ``max_y``) instead:

    to put B on top of A:   B.origin_y = A.origin_y - B.body_height_y

A 1x1 plate (3024) is 20 x 20 wide with body_height_y 8 and stud_height_y 4, so
height_y reads 12. A 1x1 brick (3005) has body_height_y 24. 1 LDU = 0.4 mm.

Usage:
    python build_part_catalog.py                     # full corpus
    python build_part_catalog.py --limit 50          # first 50 parts, for a smoke test
    python build_part_catalog.py --workers 12
    python build_part_catalog.py --build-index       # also dump the full parts/list catalogue
    python build_part_catalog.py --no-geometry       # definitions only, skip bounding boxes
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import sys
import threading
import uuid
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import requests

# Relative first so `python -m maister.database_creation.build_part_catalog`
# works; bare as a fallback so running this file directly from its own folder
# still does, which is how it was used before the package had an __init__.
try:
    from .download_ldraw_omr import (
        INHERIT_COLOR,
        MAX_DEPTH,
        make_session,
        normalize_ref,
        parse_mpd,
    )
except ImportError:
    from download_ldraw_omr import (
        INHERIT_COLOR,
        MAX_DEPTH,
        make_session,
        normalize_ref,
        parse_mpd,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETS_DIR = PROJECT_ROOT / "data" / "ldraw_omr_sets"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "parts"
# Every .dat pulled from the library - parts, subparts and primitives alike -
# is kept here, mirroring the library's own folder layout.
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "lego_pieces"

LIBRARY_URL = "https://library.ldraw.org/library/{status}/{folder}/{name}"
INDEX_URL = "https://library.ldraw.org/parts/list?page={page}"
# The whole official library in one archive (~145 MB). Fetching 7000+ parts and
# their primitive trees one file at a time earns an HTTP 429 within a minute, so
# the catalogue is seeded from this instead and only strays go over the network.
COMPLETE_ZIP_URL = "https://library.ldraw.org/library/updates/complete.zip"

# Where a referenced file may live, as (status, folder) pairs. Official first:
# the overwhelming majority of OMR parts are official.
SEARCH_PATH = (
    ("official", "parts"),
    ("official", "p"),
    ("unofficial", "parts"),
    ("unofficial", "p"),
)

LDU_MM = 0.4  # 1 LDraw unit = 0.4 mm

# Stud primitives: stud.dat, stud2a.dat, stud4.dat ... plus the "stug" stud
# groups (stug2x2.dat and friends) that parts use to place several at once.
STUD_PRIMITIVE_RE = re.compile(r"^(?:.*/)?stu[dg][\w-]*\.dat$", re.IGNORECASE)

# --- part header lines -------------------------------------------------------
NAME_RE = re.compile(r"^0\s+Name:\s*(.+)$", re.IGNORECASE)
AUTHOR_RE = re.compile(r"^0\s+Author:\s*(.+)$", re.IGNORECASE)
ORG_RE = re.compile(r"^0\s+!LDRAW_ORG\s+(.+)$", re.IGNORECASE)
CATEGORY_RE = re.compile(r"^0\s+!CATEGORY\s+(.+)$", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^0\s+!KEYWORDS\s+(.+)$", re.IGNORECASE)
# Anything else beginning with "0 " that is not a known meta line; the very first
# such line is the part's description ("Plate  1 x  1").
META_PREFIX_RE = re.compile(r"^0\s+(!|//|Name:|Author:|BFC\b|Official\b)", re.IGNORECASE)

# --- parts/list index --------------------------------------------------------
RECORD_SPLIT_RE = re.compile(r'wire:key="[^"]*table\.records\.')
LIST_PATH_RE = re.compile(r"\b((?:parts|p)/(?:[\w.\-]+/)*[\w.\-]+\.dat)\b", re.IGNORECASE)
LIST_URL_RE = re.compile(
    r'href="(https://library\.ldraw\.org/library/(official|unofficial)/[^"]+\.dat)"',
    re.IGNORECASE,
)
TOTAL_RESULTS_RE = re.compile(r"of\s+([\d,]+)\s+results", re.IGNORECASE)

PARTS_FIELDS = [
    "part_id",
    "dat_name",
    "aliases",
    "description",
    "category",
    "keywords",
    "author",
    "ldraw_org",
    "status",
    "source_url",
    "width_x",
    "height_y",
    "depth_z",
    "body_height_y",
    "stud_height_y",
    "min_x",
    "max_x",
    "min_y",
    "max_y",
    "min_z",
    "max_z",
    "width_mm",
    "height_mm",
    "depth_mm",
    "geometry",
    "set_count",
    "total_uses",
]

USAGE_FIELDS = ["part_id", "dat_name", "set_id", "set_number", "set_name", "quantity"]

DIMENSION_FIELDS = [
    "width_x", "height_y", "depth_z",
    "body_height_y", "stud_height_y",
    "min_x", "max_x", "min_y", "max_y", "min_z", "max_z",
    "width_mm", "height_mm", "depth_mm",
]


@dataclass
class BoundingBox:
    min_x: float = float("inf")
    min_y: float = float("inf")
    min_z: float = float("inf")
    max_x: float = float("-inf")
    max_y: float = float("-inf")
    max_z: float = float("-inf")

    def add(self, point: tuple[float, float, float]) -> None:
        x, y, z = point
        if x < self.min_x:
            self.min_x = x
        if x > self.max_x:
            self.max_x = x
        if y < self.min_y:
            self.min_y = y
        if y > self.max_y:
            self.max_y = y
        if z < self.min_z:
            self.min_z = z
        if z > self.max_z:
            self.max_z = z

    @property
    def valid(self) -> bool:
        return self.min_x <= self.max_x

    def as_row(self, body: "BoundingBox | None" = None) -> dict[str, str]:
        if not self.valid:
            return {field: "" for field in DIMENSION_FIELDS}
        body = body if body is not None and body.valid else self

        def fmt(value: float) -> str:
            # 3 decimals is well below LDraw's modelling precision and keeps the
            # CSV readable; -0.0 would otherwise show up for exact zeros.
            return f"{value + 0.0:.3f}".rstrip("0").rstrip(".") or "0"

        return {
            "min_x": fmt(self.min_x),
            "max_x": fmt(self.max_x),
            "min_y": fmt(self.min_y),
            "max_y": fmt(self.max_y),
            "min_z": fmt(self.min_z),
            "max_z": fmt(self.max_z),
            "width_x": fmt(self.max_x - self.min_x),
            "height_y": fmt(self.max_y - self.min_y),
            "depth_z": fmt(self.max_z - self.min_z),
            # height_y counts the studs, which sink into the piece above and so
            # consume no vertical space. body_height_y is the stackable height.
            "body_height_y": fmt(body.max_y - body.min_y),
            "stud_height_y": fmt(max(body.min_y - self.min_y, 0.0)),
            "width_mm": fmt((self.max_x - self.min_x) * LDU_MM),
            "height_mm": fmt((self.max_y - self.min_y) * LDU_MM),
            "depth_mm": fmt((self.max_z - self.min_z) * LDU_MM),
        }


@dataclass
class PartFile:
    """One parsed .dat: its header, its own vertices and its subfile references."""

    description: str = ""
    category: str = ""
    keywords: str = ""
    author: str = ""
    ldraw_org: str = ""
    status: str = ""
    source_url: str = ""
    points: list[tuple[float, float, float]] = field(default_factory=list)
    refs: list[tuple[str, tuple]] = field(default_factory=list)


# --- 3D helpers --------------------------------------------------------------
# A transform is (rotation as a 9-tuple in row-major order, translation 3-tuple).
IDENTITY = ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))


def compose(parent: tuple, child: tuple) -> tuple:
    """Transform of ``child`` expressed in ``parent``'s frame."""
    (a, b, c, d, e, f, g, h, i), (px, py, pz) = parent
    (A, B, C, D, E, F, G, H, I), (cx, cy, cz) = child
    rotation = (
        a * A + b * D + c * G, a * B + b * E + c * H, a * C + b * F + c * I,
        d * A + e * D + f * G, d * B + e * E + f * H, d * C + e * F + f * I,
        g * A + h * D + i * G, g * B + h * E + i * H, g * C + h * F + i * I,
    )
    translation = (
        a * cx + b * cy + c * cz + px,
        d * cx + e * cy + f * cz + py,
        g * cx + h * cy + i * cz + pz,
    )
    return rotation, translation


def apply(transform: tuple, point: tuple[float, float, float]) -> tuple[float, float, float]:
    (a, b, c, d, e, f, g, h, i), (tx, ty, tz) = transform
    x, y, z = point
    return (a * x + b * y + c * z + tx, d * x + e * y + f * z + ty, g * x + h * y + i * z + tz)


class LDrawLibrary:
    """Fetches .dat files from library.ldraw.org, caching them on disk.

    The cache doubles as the resolution memo: once a file is found under, say,
    ``official/p/``, later runs read it straight from disk with no HTTP at all.
    """

    def __init__(self, session: requests.Session, cache_dir: Path, refresh: bool = False):
        self.session = session
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.index: dict[str, tuple[str, str]] = {}  # name -> (status, folder)
        self._parsed: dict[str, PartFile | None] = {}
        self._lock = threading.Lock()
        self.misses: set[str] = set()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._map_path = cache_dir / "_resolved.json"
        if self._map_path.exists():
            try:
                self.index.update(
                    {k: tuple(v) for k, v in json.loads(self._map_path.read_text()).items()}
                )
            except (json.JSONDecodeError, ValueError):
                pass

    # -- fetching --------------------------------------------------------
    def _candidates(self, name: str) -> list[tuple[str, str]]:
        known = self.index.get(name)
        if known:
            return [known]
        # p/48 and p/8 are the hi/lo-res primitive folders, s/ holds subparts:
        # guessing the right folder first saves a wasted 404.
        if name.startswith(("48/", "8/")):
            order = (("official", "p"), ("unofficial", "p"), ("official", "parts"))
        elif name.startswith("s/"):
            order = (("official", "parts"), ("unofficial", "parts"), ("official", "p"))
        else:
            return list(SEARCH_PATH)
        return list(order) + [c for c in SEARCH_PATH if c not in order]

    def fetch(self, name: str) -> tuple[str, str, str] | None:
        """Return (text, status, url) for a referenced file, or None if unknown."""
        cache_file = self.cache_dir / name
        if cache_file.exists() and not self.refresh:
            status, folder = self.index.get(name, ("official", "parts"))
            return (
                cache_file.read_text(encoding="utf-8", errors="replace"),
                status,
                LIBRARY_URL.format(status=status, folder=folder, name=name),
            )
        if name in self.misses:
            return None

        for status, folder in self._candidates(name):
            url = LIBRARY_URL.format(status=status, folder=folder, name=name)
            try:
                response = self.session.get(url, timeout=30)
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue

            cache_file.parent.mkdir(parents=True, exist_ok=True)
            # Unique temp name: several workers routinely want the same primitive
            # at once, and a shared "<name>.part" would race on the rename.
            tmp = cache_file.with_name(f"{cache_file.name}.{uuid.uuid4().hex}.part")
            tmp.write_bytes(response.content)
            tmp.replace(cache_file)
            with self._lock:
                self.index[name] = (status, folder)
            return response.text, status, url

        with self._lock:
            self.misses.add(name)
        return None

    def save_index(self) -> None:
        self._map_path.write_text(json.dumps({k: list(v) for k, v in sorted(self.index.items())}))

    # -- parsing ---------------------------------------------------------
    def parse(self, name: str) -> PartFile | None:
        """Parse one .dat into header + local vertices + subfile references."""
        with self._lock:
            if name in self._parsed:
                return self._parsed[name]

        fetched = self.fetch(name)
        parsed: PartFile | None = None
        if fetched is not None:
            text, status, url = fetched
            parsed = self._parse_text(text)
            parsed.status = status
            parsed.source_url = url

        with self._lock:
            self._parsed[name] = parsed
        return parsed

    @staticmethod
    def _parse_text(text: str) -> PartFile:
        part = PartFile()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("0"):
                if NAME_RE.match(line):
                    continue
                if match := AUTHOR_RE.match(line):
                    part.author = part.author or match.group(1).strip()
                elif match := ORG_RE.match(line):
                    part.ldraw_org = part.ldraw_org or match.group(1).strip()
                elif match := CATEGORY_RE.match(line):
                    part.category = part.category or match.group(1).strip()
                elif match := KEYWORDS_RE.match(line):
                    extra = match.group(1).strip()
                    part.keywords = f"{part.keywords}, {extra}" if part.keywords else extra
                elif not part.description and not META_PREFIX_RE.match(line):
                    part.description = line[1:].strip()
                continue

            fields = line.split()
            kind = fields[0]

            if kind == "1" and len(fields) >= 15:
                # 1 <colour> x y z a b c d e f g h i <file>
                try:
                    values = [float(v) for v in fields[2:14]]
                except ValueError:
                    continue
                translation = (values[0], values[1], values[2])
                rotation = tuple(values[3:12])
                child = " ".join(fields[14:])
                part.refs.append((normalize_subfile(child), (rotation, translation)))

            elif kind in ("2", "3", "4"):
                # 2 = edge (2 points), 3 = triangle, 4 = quad. Type 5 optional
                # lines are skipped: their control points may sit off the surface.
                count = {"2": 2, "3": 3, "4": 4}[kind]
                needed = 2 + count * 3
                if len(fields) < needed:
                    continue
                try:
                    numbers = [float(v) for v in fields[2:needed]]
                except ValueError:
                    continue
                for index in range(count):
                    part.points.append(tuple(numbers[index * 3 : index * 3 + 3]))

        # LDraw only writes !CATEGORY when it differs from the default rule:
        # "the category is the first word of the description". Apply that rule so
        # 3024 "Plate 1 x 1" comes out as Plate rather than blank.
        if not part.category and part.description:
            part.category = part.description.split()[0].strip("_~=")

        return part

    # -- geometry --------------------------------------------------------
    def bounding_box(self, name: str) -> tuple[BoundingBox, BoundingBox]:
        """Return (full box, body box) for a part, subfile tree expanded.

        The body box excludes everything under a stud primitive. Studs are the
        only geometry that does not consume vertical space when stacking - they
        sink into the tube of the piece above - so the body box is what gives a
        usable height. Detecting them by primitive beats assuming "whatever sits
        above the origin is a stud": parts such as 54200 (Slope 1 x 1 x 0.667)
        carry no studs at all and sit entirely above their origin.
        """
        full = BoundingBox()
        body = BoundingBox()

        def walk(
            current: str, transform: tuple, stack: frozenset[str], depth: int, in_stud: bool
        ) -> None:
            if depth > MAX_DEPTH or current in stack:
                return
            part = self.parse(current)
            if part is None:
                return
            for point in part.points:
                placed = apply(transform, point)
                full.add(placed)
                # Only a stud sitting ABOVE the origin (negative y) protrudes and
                # gets discounted. The same stud primitives also model underside
                # tubes - 6141 (Plate 1 x 1 Round) builds its base from stud4.dat -
                # and that geometry is body, not overhang.
                if not (in_stud and placed[1] < 0.0):
                    body.add(placed)
            for child_name, child_transform in part.refs:
                walk(
                    child_name,
                    compose(transform, child_transform),
                    stack | {current},
                    depth + 1,
                    in_stud or bool(STUD_PRIMITIVE_RE.match(child_name)),
                )

        walk(name, IDENTITY, frozenset(), 0, False)
        return full, body


def normalize_subfile(name: str) -> str:
    """``s\\3024s01.dat`` -> ``s/3024s01.dat``; keeps the subfolder, drops case."""
    return name.strip().replace("\\", "/").lstrip("./").lower()


# Parts inlined into an MPD are often renamed after the set that carries them,
# e.g. "10019 - 2680b.dat". The library only knows the bare mould, "2680b.dat".
INLINED_NAME_RE = re.compile(r"^.*\S\s+-\s+(\S+\.dat)$", re.IGNORECASE)


def library_name(dat_name: str) -> str:
    """The name to look up in the library for a part referenced by a model."""
    match = INLINED_NAME_RE.match(dat_name)
    return match.group(1).lower() if match else dat_name


def bootstrap_from_zip(
    library: LDrawLibrary, session: requests.Session, keep_zip: bool = False
) -> int:
    """Seed ``data/lego_pieces/`` from the official complete library archive.

    Extracts ``parts/`` and ``p/`` (subparts and primitives included) into the
    same flat cache layout ``fetch()`` uses, so afterwards nearly every lookup is
    a local file read.
    """
    marker = library.cache_dir / "_bootstrap.done"
    if marker.exists() and not library.refresh:
        print(f"Bootstrap: already seeded ({marker.read_text().strip()}); skipping")
        return 0

    archive_path = library.cache_dir / "_complete.zip"
    if not archive_path.exists():
        print(f"Downloading {COMPLETE_ZIP_URL}")
        tmp = archive_path.with_name("_complete.zip.part")
        with session.get(COMPLETE_ZIP_URL, stream=True, timeout=600) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0))
            done = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(1 << 20):
                    handle.write(chunk)
                    done += len(chunk)
                    print(
                        f"\r  {done / 1e6:.0f} MB" + (f" / {total / 1e6:.0f} MB" if total else ""),
                        end="",
                        flush=True,
                    )
        tmp.replace(archive_path)
        print()

    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        # "p" first, then "parts", so a part wins any rare name clash - the same
        # precedence fetch() uses when probing the library.
        for folder in ("p", "parts"):
            prefix = f"ldraw/{folder}/"
            for info in entries:
                if info.is_dir():
                    continue
                lower = info.filename.lower()
                if not lower.startswith(prefix) or not lower.endswith(".dat"):
                    continue
                name = lower[len(prefix) :]
                destination = library.cache_dir / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as out:
                    shutil.copyfileobj(source, out)
                library.index[name] = ("official", folder)
                extracted += 1
                if extracted % 2000 == 0:
                    print(f"\r  extracted {extracted} file(s)", end="", flush=True)

    library.save_index()
    marker.write_text(f"{extracted} .dat file(s) from complete.zip")
    if not keep_zip:
        archive_path.unlink()
    print(f"\rBootstrap: {extracted} .dat file(s) into {library.cache_dir}")
    return extracted


# --- corpus scan -------------------------------------------------------------
@dataclass
class SetRef:
    set_id: str
    set_number: str
    set_name: str


def read_metadata(sets_dir: Path) -> dict[str, SetRef]:
    """``metadata.csv`` as {file_name: SetRef}."""
    csv_path = sets_dir / "metadata.csv"
    if not csv_path.exists():
        raise SystemExit(f"error: {csv_path} not found; run download_ldraw_omr.py first")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return {
            row["file_name"]: SetRef(row["set_id"], row["set_number"], row["set_name"])
            for row in csv.DictReader(handle)
        }


def parts_of_model(path: Path) -> Counter:
    """Count the .dat parts of one model, expanding submodels recursively.

    Mirrors ``download_ldraw_omr.count_pieces`` but keeps the part names instead
    of only tallying them.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    order, blocks = parse_mpd(text)
    if not order:
        return Counter()

    counts: Counter = Counter()

    def walk(name: str, stack: frozenset[str], depth: int) -> None:
        block = blocks.get(name)
        if block is None or block[0]:  # not a submodel of this file -> a real part
            if name.endswith(".dat"):
                counts[name] += 1
            return
        if name in stack or depth > MAX_DEPTH:
            return
        for _, ref_name in block[1]:
            walk(ref_name, stack | {name}, depth + 1)

    roots = [order[0]]
    if not blocks[order[0]][1]:
        referenced = {ref for _, refs in blocks.values() for _, ref in refs}
        roots = [name for name in order if name not in referenced and blocks[name][1]]

    for root in roots:
        for _, ref_name in blocks[root][1]:
            walk(ref_name, frozenset({root}), 1)
    return counts


def scan_corpus(sets_dir: Path, metadata: dict[str, SetRef]) -> dict[str, dict[str, int]]:
    """{dat_name: {file_name: quantity}} across every model on disk."""
    usage: dict[str, dict[str, int]] = defaultdict(dict)
    missing = 0
    for file_name in sorted(metadata):
        path = sets_dir / file_name
        if not path.exists():
            missing += 1
            continue
        for dat_name, quantity in parts_of_model(path).items():
            usage[dat_name][file_name] = quantity

    # Files present on disk but absent from metadata.csv have no set_id, so they
    # cannot be attributed to a set; flag them rather than dropping them quietly.
    unlisted = sorted(
        path.name
        for path in sets_dir.glob("*.mpd")
        if path.name not in metadata
    )

    print(
        f"Corpus: {len(metadata) - missing} model(s) scanned, "
        f"{len(usage)} unique .dat part(s)"
        + (f", {missing} listed but missing on disk" if missing else "")
    )
    if unlisted:
        print(
            f"  warning: {len(unlisted)} .mpd file(s) on disk are absent from metadata.csv "
            f"and were skipped (no set_id), e.g. {unlisted[:3]}\n"
            f"  run 'python download_ldraw_omr.py --metadata-only' to index them"
        )
    return usage


# --- parts/list index (optional) ---------------------------------------------
def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style|svg)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", "|", fragment)
    return html.unescape(re.sub(r"[ \t]+", " ", fragment))


def build_index(session: requests.Session, out_path: Path, max_pages: int) -> int:
    """Walk https://library.ldraw.org/parts/list and dump the whole catalogue.

    25 rows per page is fixed server-side, so the full library is ~1,800 requests.
    This is optional: the catalogue build resolves parts by path instead.
    """
    rows: dict[str, dict[str, str]] = {}
    page = 1
    total: int | None = None
    while page <= max_pages:
        response = session.get(INDEX_URL.format(page=page), timeout=30)
        response.raise_for_status()
        body = response.text
        total = total or (
            int(match.group(1).replace(",", ""))
            if (match := TOTAL_RESULTS_RE.search(body))
            else None
        )

        found = 0
        for block in RECORD_SPLIT_RE.split(body)[1:]:
            url_match = LIST_URL_RE.search(block)
            path_match = LIST_PATH_RE.search(strip_tags(block))
            if not (url_match and path_match):
                continue
            cells = [c.strip() for c in strip_tags(block).split("|") if c.strip()]
            # cells: <record id junk>, path, description, updated, Official/Unofficial
            description = cells[2] if len(cells) > 2 else ""
            rows[path_match.group(1).lower()] = {
                "path": path_match.group(1).lower(),
                "part_id": Path(path_match.group(1)).stem,
                "description": description,
                "status": url_match.group(2),
                "url": url_match.group(1),
            }
            found += 1

        if not found:
            break
        print(f"\rparts/list page {page} - {len(rows)} record(s)", end="", flush=True)
        if total is not None and len(rows) >= total:
            break
        page += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "part_id", "description", "status", "url"])
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])
    print(f"\nIndex: {len(rows)} record(s) -> {out_path}")
    return 0


# --- output ------------------------------------------------------------------
def write_outputs(
    out_dir: Path,
    parts: list[dict[str, str]],
    usage: dict[str, dict[str, int]],
    metadata: dict[str, SetRef],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = out_dir / "parts_catalog.csv"
    with catalog_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PARTS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(parts, key=lambda r: r["part_id"]):
            writer.writerow({field: row.get(field, "") for field in PARTS_FIELDS})

    usage_path = out_dir / "part_set_usage.csv"
    with usage_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=USAGE_FIELDS)
        writer.writeheader()
        rows = 0
        for row in sorted(parts, key=lambda r: r["part_id"]):
            dat_name = row["dat_name"]
            for file_name, quantity in sorted(usage.get(dat_name, {}).items()):
                ref = metadata[file_name]
                writer.writerow(
                    {
                        "part_id": row["part_id"],
                        "dat_name": dat_name,
                        "set_id": ref.set_id,
                        "set_number": ref.set_number,
                        "set_name": ref.set_name,
                        "quantity": quantity,
                    }
                )
                rows += 1

    print(f"Wrote {len(parts)} part(s) -> {catalog_path}")
    print(f"Wrote {rows} (part, set) row(s) -> {usage_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR, help="folder with the .mpd corpus")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="destination for the CSVs")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="downloaded .dat cache")
    parser.add_argument("--workers", type=int, default=4, help="parallel part lookups (default 4)")
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="do not seed the cache from complete.zip (crawls file by file; expect HTTP 429)",
    )
    parser.add_argument("--keep-zip", action="store_true", help="keep complete.zip after extracting")
    parser.add_argument("--limit", type=int, default=0, help="only process the first N parts")
    parser.add_argument("--refresh", action="store_true", help="re-download cached .dat files")
    parser.add_argument("--no-geometry", action="store_true", help="skip bounding boxes (definitions only)")
    parser.add_argument("--build-index", action="store_true", help="dump the full parts/list catalogue and exit")
    parser.add_argument("--index-pages", type=int, default=2000, help="max parts/list pages to walk")
    args = parser.parse_args(argv)

    session = make_session()

    if args.build_index:
        return build_index(session, args.out_dir / "ldraw_parts_index.csv", args.index_pages)

    metadata = read_metadata(args.sets_dir)
    usage = scan_corpus(args.sets_dir, metadata)

    dat_names = sorted(usage)
    if args.limit:
        dat_names = dat_names[: args.limit]

    library = LDrawLibrary(session, args.cache_dir, refresh=args.refresh)
    if not args.no_bootstrap:
        bootstrap_from_zip(library, session, keep_zip=args.keep_zip)

    parts: list[dict[str, str]] = []
    unresolved: list[str] = []
    done = 0
    lock = threading.Lock()

    def resolve(dat_name: str) -> str | None:
        """The library file a referenced name maps to, or None if unknown."""
        if library.parse(dat_name) is not None:
            return dat_name
        # Models frequently inline a part under a set-prefixed name; retry
        # against the bare mould before giving up on it.
        stripped = library_name(dat_name)
        if stripped != dat_name and library.parse(stripped) is not None:
            return stripped
        return None

    print(f"Resolving {len(dat_names)} part(s) against library.ldraw.org (cache: {args.cache_dir})")
    resolution: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(resolve, name): name for name in dat_names}
        for future in as_completed(futures):
            dat_name = futures[future]
            try:
                lookup = future.result()
            except Exception as exc:  # network / parse problems on one part
                print(f"\n[{dat_name}] ERROR: {exc}", file=sys.stderr)
                lookup = None

            with lock:
                done += 1
                if lookup is None:
                    unresolved.append(dat_name)
                else:
                    resolution[dat_name] = lookup
                if done % 250 == 0 or done == len(dat_names):
                    print(f"\r  {done}/{len(dat_names)} resolved", end="", flush=True)
    print()

    # One row per mould, not per referencing name: "10019 - 2680b.dat" and
    # "2680b.dat" are the same piece, and their set usage has to be pooled or
    # every alias reports a fraction of the truth.
    merged: dict[str, dict[str, int]] = defaultdict(dict)
    aliases: dict[str, set[str]] = defaultdict(set)
    for dat_name, lookup in resolution.items():
        for file_name, quantity in usage[dat_name].items():
            merged[lookup][file_name] = merged[lookup].get(file_name, 0) + quantity
        if dat_name != lookup:
            aliases[lookup].add(dat_name)

    print(f"Measuring {len(merged)} unique piece(s)")

    def build_row(lookup: str) -> dict[str, str]:
        part = library.parse(lookup)
        row: dict[str, str] = {
            "part_id": Path(lookup).stem,
            "dat_name": lookup,
            "aliases": ";".join(sorted(aliases.get(lookup, ()))),
            "description": part.description,
            "category": part.category,
            "keywords": part.keywords,
            "author": part.author,
            "ldraw_org": part.ldraw_org,
            "status": part.status,
            "source_url": part.source_url,
            "set_count": str(len(merged[lookup])),
            "total_uses": str(sum(merged[lookup].values())),
        }
        if args.no_geometry:
            row["geometry"] = "skipped"
        else:
            box, body = library.bounding_box(lookup)
            row.update(box.as_row(body))
            row["geometry"] = "ok" if box.valid else "empty"
        return row

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(build_row, name): name for name in sorted(merged)}
        for future in as_completed(futures):
            try:
                parts.append(future.result())
            except Exception as exc:
                print(f"\n[{futures[future]}] ERROR: {exc}", file=sys.stderr)
            with lock:
                done += 1
                if done % 250 == 0 or done == len(merged):
                    print(f"\r  {done}/{len(merged)} measured", end="", flush=True)
    print()
    usage_by_dat, usage = usage, merged  # keep the per-reference tallies for the report

    library.save_index()
    write_outputs(args.out_dir, parts, usage, metadata)

    if unresolved:
        # Mostly parts a model defines inline and reshapes (flexed hoses, custom
        # stickers), which have no library counterpart. Listed rather than merely
        # counted, so the gap is auditable.
        path = args.out_dir / "unresolved_parts.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["dat_name", "set_count", "total_uses"])
            for name in sorted(unresolved):
                writer.writerow(
                    [name, len(usage_by_dat[name]), sum(usage_by_dat[name].values())]
                )
        print(f"{len(unresolved)} part(s) not found in the library -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
