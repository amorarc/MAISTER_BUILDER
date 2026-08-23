"""Download LDraw OMR model files (.mpd) from https://library.ldraw.org/omr/sets/<id>.

Each OMR set page (id 1..2000 by default) contains a heading of the form
``31199-1 - Marvel Studios Iron Man`` plus zero or more "Download" links pointing
at ``https://library.ldraw.org/library/omr/<file>.mpd``.

Files are stored in ``data/ldraw_omr_sets/`` named ``<set number>_<set name>.mpd``
(with a ``__<variant>`` suffix when a set publishes several models), and every
downloaded model is recorded in ``metadata.csv`` next to them, together with:

* ``theme`` / ``year``  - scraped from the OMR sets index (temporal properties)
* ``total_pieces``      - parts in the model, submodels expanded recursively
* ``unique_pieces``     - distinct part moulds (colour ignored)
* ``unique_pieces_by_color`` - distinct (part, colour) combinations

Usage:
    python download_ldraw_omr.py                    # ids 1..4000
    python download_ldraw_omr.py --start 1 --end 500 --workers 8
    python download_ldraw_omr.py --overwrite        # re-download existing files
    python download_ldraw_omr.py --metadata-only    # rebuild metadata.csv from files on disk
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://library.ldraw.org/omr/sets/{set_id}"
INDEX_URL = "https://library.ldraw.org/omr/sets?page={page}"
USER_AGENT = "maister-builder/1.0 (LDraw OMR dataset builder)"

# Project root: .../maister_builder (this file lives in maister/database_creation/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "ldraw_omr_sets"

DOWNLOAD_RE = re.compile(
    r'href="(https://library\.ldraw\.org/library/omr/[^"]+\.mpd)"', re.IGNORECASE
)
# The page heading, e.g. "349-1 - Swiss Chalet" / "31199-1 - Marvel Studios Iron Man"
HEADING_RE = re.compile(
    r">\s*([0-9][\w.]*-\d+)\s*-\s*([^<>]+?)\s*<", re.MULTILINE
)
TITLE_RE = re.compile(
    r"<title>\s*LDraw\.org Official Model Repository\s*-\s*(.*?)\s*</title>",
    re.IGNORECASE | re.DOTALL,
)

# Sets index table: rows carry the record id plus image/number/name/theme/year/models cells
ROW_ID_RE = re.compile(r"table\.records?\.(\d+)")
CELL_RE = re.compile(r"(?is)<td\b.*?</td>")
TOTAL_RESULTS_RE = re.compile(r"of\s+([\d,]+)\s+results", re.IGNORECASE)

# LDraw file lines
FILE_LINE_RE = re.compile(r"^0\s+FILE\s+(.+)$", re.IGNORECASE)
ORG_LINE_RE = re.compile(r"^0\s+!LDRAW_ORG\s+(.+)$", re.IGNORECASE)
# Anything that is not a model in an MPD (Part, Subpart, Primitive, Shortcut...)
NON_MODEL_ORG_RE = re.compile(r"part|primitive|shortcut", re.IGNORECASE)

INHERIT_COLOR = "16"  # LDraw "use the parent's colour"
IMPLICIT_MAIN = "\x00main"  # holds content of files that have no 0 FILE header
MAX_DEPTH = 64  # guard against pathological / self-referencing MPDs

CSV_FIELDS = [
    "set_id",
    "set_number",
    "set_name",
    "theme",
    "year",
    "total_pieces",
    "unique_pieces",
    "unique_pieces_by_color",
    "file_name",
    "source_url",
]


@dataclass
class SetInfo:
    """Theme and release year for one set, taken from the OMR sets index."""

    theme: str = ""
    year: str = ""


@dataclass
class PieceCounts:
    total: int = 0
    unique: int = 0
    unique_by_color: int = 0


@dataclass
class ModelRecord:
    set_id: int
    set_number: str
    set_name: str
    file_name: str
    source_url: str
    theme: str = ""
    year: str = ""
    counts: PieceCounts = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.counts is None:
            self.counts = PieceCounts()


def make_session() -> requests.Session:
    """Session with connection pooling and retries on transient failures."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def slugify(name: str) -> str:
    """Turn a set name into a safe file-name fragment: 'Swiss Chalet' -> 'Swiss-Chalet'."""
    name = html.unescape(name)
    name = re.sub(r"[^\w\s.-]", "", name, flags=re.UNICODE)
    name = re.sub(r"[\s_]+", "-", name.strip())
    name = re.sub(r"-{2,}", "-", name).strip("-.")
    return name[:120] or "unnamed"


def parse_page(page_html: str) -> tuple[str | None, str | None, list[str]]:
    """Return (set_number, set_name, download_urls) parsed from a set page."""
    set_number = set_name = None

    heading = HEADING_RE.search(page_html)
    if heading:
        set_number = html.unescape(heading.group(1)).strip()
        set_name = html.unescape(heading.group(2)).strip()

    if not set_name:
        title = TITLE_RE.search(page_html)
        if title:
            set_name = html.unescape(title.group(1)).strip()

    # dict.fromkeys keeps page order while dropping duplicates
    urls = list(dict.fromkeys(DOWNLOAD_RE.findall(page_html)))
    return set_number, set_name, urls


def strip_tags(fragment: str) -> str:
    """Plain text of an HTML fragment."""
    fragment = re.sub(r"(?is)<(script|style|svg)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?s)<!--.*?-->", " ", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return html.unescape(re.sub(r"\s+", " ", fragment)).strip()


def parse_index_page(page_html: str) -> tuple[dict[int, SetInfo], int | None]:
    """Parse one page of the sets index into {set_id: SetInfo} plus the total row count."""
    infos: dict[int, SetInfo] = {}
    for block in re.split(r"(?i)<tr\b", page_html)[1:]:
        row_id = ROW_ID_RE.search(block)
        if not row_id:
            continue
        cells = [strip_tags(cell) for cell in CELL_RE.findall(block)]
        # cells: image, number, name, theme, year, models
        if len(cells) < 5:
            continue
        year = cells[4] if re.fullmatch(r"\d{4}", cells[4]) else ""
        infos[int(row_id.group(1))] = SetInfo(theme=cells[3], year=year)

    total_match = TOTAL_RESULTS_RE.search(page_html)
    total = int(total_match.group(1).replace(",", "")) if total_match else None
    return infos, total


def fetch_set_index(session: requests.Session, max_pages: int = 200) -> dict[int, SetInfo]:
    """Walk the paginated OMR sets index to collect theme and year for every set."""
    infos: dict[int, SetInfo] = {}
    page = 1
    total: int | None = None
    while page <= max_pages:
        response = session.get(INDEX_URL.format(page=page), timeout=30)
        response.raise_for_status()
        page_infos, page_total = parse_index_page(response.text)
        if not page_infos:
            break
        infos.update(page_infos)
        total = page_total or total
        if total is not None and len(infos) >= total:
            break
        page += 1
    print(f"Sets index: theme/year for {len(infos)} sets ({page} page(s))")
    return infos


def parse_mpd(text: str) -> tuple[list[str], dict[str, tuple[bool, list[tuple[str, str]]]]]:
    """Split an MPD into its sub-files.

    Returns (order of sub-file names, {name: (is_part, [(color, referenced name), ...])}).
    ``is_part`` marks blocks that embed an actual part rather than a submodel - those
    count as a single piece instead of being expanded.

    Some OMR downloads are plain single-model .ldr files with no ``0 FILE`` header at
    all; their content is collected into one implicit main model.
    """
    order: list[str] = []
    blocks: dict[str, tuple[bool, list[tuple[str, str]]]] = {}
    current = IMPLICIT_MAIN
    is_part = False
    refs: list[tuple[str, str]] = []

    def flush() -> None:
        if current not in blocks:
            blocks[current] = (is_part, refs)
            order.append(current)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        file_match = FILE_LINE_RE.match(line)
        if file_match:
            flush()
            current = normalize_ref(file_match.group(1))
            is_part, refs = False, []
            continue

        org_match = ORG_LINE_RE.match(line)
        if org_match and NON_MODEL_ORG_RE.search(org_match.group(1)):
            is_part = True
        elif line.startswith("1 "):
            # 1 <colour> x y z a b c d e f g h i <file>
            fields = line.split(None, 14)
            if len(fields) == 15:
                refs.append((fields[1], normalize_ref(fields[14])))

    flush()

    # Drop the implicit block unless it actually held the model (proper MPDs start with 0 FILE)
    if len(order) > 1 and not blocks[IMPLICIT_MAIN][1]:
        order.remove(IMPLICIT_MAIN)
        del blocks[IMPLICIT_MAIN]
    return order, blocks


def normalize_ref(name: str) -> str:
    """Canonical form of a referenced file name (case- and separator-insensitive)."""
    return name.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()


def count_pieces(path: Path) -> PieceCounts:
    """Count the pieces of an MPD by expanding its submodel tree.

    Only leaves - references that are not submodels defined inside the same MPD - are
    counted, so a submodel used three times contributes its parts three times.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    order, blocks = parse_mpd(text)
    if not order:
        return PieceCounts()

    counts = PieceCounts()
    unique: set[str] = set()
    unique_colored: set[tuple[str, str]] = set()

    def walk(name: str, color: str, stack: frozenset[str], depth: int) -> None:
        block = blocks.get(name)
        if block is None or block[0]:  # a part, not a submodel -> one piece
            counts.total += 1
            unique.add(name)
            unique_colored.add((name, color))
            return
        if name in stack or depth > MAX_DEPTH:  # circular reference
            return
        for ref_color, ref_name in block[1]:
            # colour 16 means "inherit from whoever referenced me"
            walk(ref_name, color if ref_color == INHERIT_COLOR else ref_color, stack | {name}, depth + 1)

    roots = [order[0]]
    if not blocks[order[0]][1]:
        # A few OMR files declare an empty main model (e.g. 6360-1, whose main is a stray
        # "mian.ldr"); fall back to every submodel nothing else references.
        referenced = {ref for _, refs in blocks.values() for _, ref in refs}
        roots = [name for name in order if name not in referenced and blocks[name][1]]

    for root in roots:
        for ref_color, ref_name in blocks[root][1]:
            walk(ref_name, ref_color, frozenset({root}), 1)

    counts.unique = len(unique)
    counts.unique_by_color = len(unique_colored)
    return counts


def target_name(set_number: str, set_name: str, url: str, multiple: bool) -> str:
    """Build '<set number>_<Set-Name>[__<variant>].mpd' for a download URL."""
    base = f"{set_number}_{slugify(set_name)}"
    if not multiple:
        return f"{base}.mpd"

    stem = url.rsplit("/", 1)[-1][: -len(".mpd")]
    # Remote files look like '31199-1_Hulkbuster-Mark-I' - keep the variant part only.
    variant = stem[len(set_number):].lstrip("_-") if stem.startswith(set_number) else stem
    return f"{base}__{slugify(variant)}.mpd" if variant else f"{base}.mpd"


def fetch_set(
    session: requests.Session,
    set_id: int,
    out_dir: Path,
    overwrite: bool,
    delay: float,
    set_index: dict[int, SetInfo],
) -> tuple[list[ModelRecord], str]:
    """Download every model of one OMR set. Returns (records, status message)."""
    url = BASE_URL.format(set_id=set_id)
    response = session.get(url, timeout=30)
    if response.status_code == 404:
        return [], "not found"
    response.raise_for_status()

    set_number, set_name, download_urls = parse_page(response.text)
    if not download_urls:
        return [], f"no models ({set_number or '?'} - {set_name or '?'})"
    if not set_number:
        set_number = str(set_id)
    if not set_name:
        set_name = "unknown"

    info = set_index.get(set_id, SetInfo())
    records: list[ModelRecord] = []
    multiple = len(download_urls) > 1
    for model_url in download_urls:
        file_name = target_name(set_number, set_name, model_url, multiple)
        destination = out_dir / file_name

        if not destination.exists() or overwrite:
            if delay:
                time.sleep(delay)
            model_response = session.get(model_url, timeout=60)
            model_response.raise_for_status()

            # Write to a temp file first so an interrupted run leaves no partial .mpd
            tmp = destination.with_suffix(".mpd.part")
            tmp.write_bytes(model_response.content)
            tmp.replace(destination)

        records.append(
            ModelRecord(
                set_id,
                set_number,
                set_name,
                file_name,
                model_url,
                theme=info.theme,
                year=info.year,
                counts=count_pieces(destination),
            )
        )

    return records, f"{len(records)} model(s): {set_number} - {set_name} ({info.year or 'year ?'})"


def write_metadata(csv_path: Path, records: list[ModelRecord]) -> None:
    """Merge records into metadata.csv, keyed by file name."""
    rows: dict[str, dict[str, str]] = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows[row["file_name"]] = row

    for record in records:
        rows[record.file_name] = {
            "set_id": str(record.set_id),
            "set_number": record.set_number,
            "set_name": record.set_name,
            "theme": record.theme,
            "year": record.year,
            "total_pieces": str(record.counts.total),
            "unique_pieces": str(record.counts.unique),
            "unique_pieces_by_color": str(record.counts.unique_by_color),
            "file_name": record.file_name,
            "source_url": record.source_url,
        }

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(rows, key=lambda k: (int(rows[k]["set_id"]), k)):
            row = rows[key]
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def rebuild_metadata(out_dir: Path, set_index: dict[int, SetInfo]) -> int:
    """Recompute metadata.csv for the .mpd files already on disk (no model downloads)."""
    csv_path = out_dir / "metadata.csv"
    if not csv_path.exists():
        print(f"No metadata.csv in {out_dir}; run a download first.", file=sys.stderr)
        return 1

    with csv_path.open(newline="", encoding="utf-8") as handle:
        existing = list(csv.DictReader(handle))

    records: list[ModelRecord] = []
    missing = 0
    for row in existing:
        path = out_dir / row["file_name"]
        if not path.exists():
            missing += 1
            continue
        set_id = int(row["set_id"])
        info = set_index.get(set_id, SetInfo(theme=row.get("theme", ""), year=row.get("year", "")))
        records.append(
            ModelRecord(
                set_id,
                row["set_number"],
                row["set_name"],
                row["file_name"],
                row["source_url"],
                theme=info.theme,
                year=info.year,
                counts=count_pieces(path),
            )
        )

    write_metadata(csv_path, records)
    dated = sum(1 for record in records if record.year)
    print(
        f"Rebuilt {csv_path} for {len(records)} model(s); "
        f"{dated} with a year, {missing} file(s) listed but missing on disk."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--start", type=int, default=1, help="first set id (default 1)")
    parser.add_argument("--end", type=int, default=5000, help="last set id, inclusive (default 5000)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="destination folder")
    parser.add_argument("--workers", type=int, default=4, help="parallel set pages (default 4)")
    parser.add_argument("--delay", type=float, default=0.2, help="pause before each file download, seconds")
    parser.add_argument("--overwrite", action="store_true", help="re-download files that already exist")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="recompute piece counts and theme/year for files already downloaded",
    )
    parser.add_argument("--no-index", action="store_true", help="skip the sets index (no theme/year)")
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    session = make_session()
    set_index: dict[int, SetInfo] = {} if args.no_index else fetch_set_index(session)

    if args.metadata_only:
        return rebuild_metadata(out_dir, set_index)

    lock = threading.Lock()
    all_records: list[ModelRecord] = []
    downloaded = failed = empty = 0

    print(f"Downloading OMR sets {args.start}..{args.end} into {out_dir}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                fetch_set, session, set_id, out_dir, args.overwrite, args.delay, set_index
            ): set_id
            for set_id in range(args.start, args.end + 1)
        }
        for future in as_completed(futures):
            set_id = futures[future]
            try:
                records, status = future.result()
            except Exception as exc:  # network / HTTP / disk problems
                failed += 1
                print(f"[{set_id:>5}] ERROR: {exc}", file=sys.stderr)
                continue

            if records:
                with lock:
                    all_records.extend(records)
                downloaded += len(records)
            else:
                empty += 1
            print(f"[{set_id:>5}] {status}")

    write_metadata(out_dir / "metadata.csv", all_records)
    print(
        f"\nDone. {downloaded} model file(s) from {len(futures) - empty - failed} set(s); "
        f"{empty} set(s) without models, {failed} error(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
