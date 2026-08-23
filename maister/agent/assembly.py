"""Putting finished subconstructions together into one scene.

Each subbuild is a model in its own right, written and validated on its own,
with its own idea of where the origin is. Assembling them is three problems:

* **Inlining.** An MPD holds every block it needs. A subbuild that is itself an
  MPD brings its own blocks along, and two subbuilds that both call a block
  ``base.ldr`` would otherwise silently share one - so every block is renamed
  into its subbuild's namespace and every reference to it rewritten.
* **Placement.** Where each one goes. Computed from real bounding boxes rather
  than guessed: they are laid in a row, each dropped onto the ground plane and
  snapped to the stud grid, with a stud of air between them. An explicit
  placement always wins over the computed one.
* **Not doing it by hand.** The agent could write this MPD itself, and the
  arithmetic is exactly the kind it gets wrong at the end of a long run. It is
  deterministic, so it is code.

The result is a normal MPD: main model first, one block per subconstruction,
and it validates like anything else.
"""

import re
from pathlib import Path

from . import geometry

_FILE = re.compile(r"^\s*0\s+FILE\s+(.+?)\s*$", re.IGNORECASE)
_NOFILE = re.compile(r"^\s*0\s+NOFILE\s*$", re.IGNORECASE)
# A type-1 line: colour, 12 numbers, then the file it references.
_REF = re.compile(r"^(\s*1(?:\s+\S+){13}\s+)(.+?)\s*$")

HEADER = ("0 Name: {name}\n"
          "0 Author: Maister Builder\n"
          "0 !LDRAW_ORG Model\n"
          "0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt\n")


def _norm(name):
    return str(name or "").strip().replace("\\", "/").lower()


def read_blocks(text, fallback_name):
    """``text`` split into ``[(block_name, [lines])]``, main block first.

    A plain .ldr with no ``0 FILE`` at all is one block under
    ``fallback_name`` - which is what most subbuilds are.
    """
    blocks, current, lines = [], None, []

    for line in text.splitlines():
        found = _FILE.match(line)
        if found:
            if current is not None or lines:
                blocks.append((current or fallback_name, lines))
            current, lines = found.group(1).strip(), []
            continue
        if _NOFILE.match(line):
            if current is not None or lines:
                blocks.append((current or fallback_name, lines))
            current, lines = None, []
            continue
        lines.append(line)

    if current is not None or lines:
        blocks.append((current or fallback_name, lines))

    return [(name, body) for name, body in blocks
            if any(ln.strip() for ln in body)] or [(fallback_name, [])]


def namespace(text, prefix, fallback_name):
    """Rename every block in ``text`` into ``prefix``'s namespace.

    The first block becomes ``<prefix>.ldr`` - that is the name the scene will
    reference - and the rest become ``<prefix>-<their name>``. References
    between them are rewritten to match; references to anything else (a real
    LDraw part) are left exactly as they are.

    Returns ``[(new_name, [lines])]``.
    """
    blocks = read_blocks(text, fallback_name)

    renames = {}
    for index, (name, _) in enumerate(blocks):
        stem = re.sub(r"\.(ldr|dat|mpd)$", "", name, flags=re.IGNORECASE)
        stem = re.sub(r"[^\w.-]+", "-", stem).strip("-") or "block"
        renames[_norm(name)] = (f"{prefix}.ldr" if index == 0
                                else f"{prefix}-{stem}.ldr")

    out = []
    for name, body in blocks:
        rewritten = []
        for line in body:
            found = _REF.match(line)
            if found:
                target = renames.get(_norm(found.group(2)))
                if target:
                    line = found.group(1) + target
            rewritten.append(line)
        out.append((renames[_norm(name)], rewritten))
    return out


# No \b after "Name:" - a word boundary needs a word character on one side, and
# between the colon and the space that follows it there is none, so the line
# would never match and every block would carry two Name: headers.
_META = re.compile(r"^\s*0\s+(Name:|Author:|!LDRAW_ORG\b|!LICENSE\b|!LPUB\b)",
                   re.IGNORECASE)
_COMMENT = re.compile(r"^\s*0\s+(?!!|FILE\b|STEP\b|Name:|Author:)\S")


def _strip_meta(lines):
    """Drop a block's own header lines; the scene writes its own."""
    return [ln for ln in lines if not _META.match(ln)]


def _description(lines, default):
    """Split a block into its description line and the rest.

    LDraw puts the description first, above ``0 Name:``. A block that already
    carries one keeps it - lifted to where it belongs rather than left below
    the header and duplicated by one made from the component's name.
    """
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _COMMENT.match(line):
            return line, lines[:index] + lines[index + 1:]
        break
    return f"0 {default}", list(lines)


def compose(components, title="Scene", main_name="main.ldr", spacing=None):
    """Build one MPD from several subbuild files.

    ``components`` is a list of dicts, each with:

        file        path to the subbuild's LDraw source (required)
        name        what to call it in the scene; defaults to the file's stem
        x, y, z     explicit placement in LDU; omit to have it computed
        colour      LDraw colour code for the reference line, default 16

    Returns ``(text, report)``. The report says where each component was put and
    how big it was, which is what the agent needs in order to say anything true
    about the scene afterwards.
    """
    resolved, taken = [], set()
    for spec in components or []:
        path = Path(spec["file"])
        if not path.is_file():
            return None, {"error": f"no such subbuild file: {spec['file']}"}

        stem = re.sub(r"[^\w-]+", "-", str(spec.get("name") or path.stem)).strip("-")
        stem = (stem or "part").lower()[:40]
        base, n = stem, 2
        while stem in taken:
            stem, n = f"{base}-{n}", n + 1
        taken.add(stem)

        text = path.read_text(encoding="utf-8", errors="replace")
        resolved.append({
            "name": stem,
            "path": path,
            "text": text,
            "measure": geometry.measure(path),
            "given": {k: spec[k] for k in ("x", "y", "z") if spec.get(k) is not None},
            "colour": str(spec.get("colour") or 16),
        })

    if not resolved:
        return None, {"error": "no components to assemble"}

    kwargs = {"spacing": spacing} if spacing is not None else {}
    computed = geometry.layout([(c["name"], c["measure"]) for c in resolved],
                               **kwargs)

    lines = [f"0 FILE {main_name}", f"0 {title}",
             HEADER.format(name=main_name).rstrip()]
    placements = []

    for component in resolved:
        dx, dy, dz = computed.get(component["name"], (0.0, 0.0, 0.0))
        given = component["given"]
        x = float(given.get("x", dx))
        y = float(given.get("y", dy))
        z = float(given.get("z", dz))
        lines.append(f"1 {component['colour']} {_n(x)} {_n(y)} {_n(z)} "
                     f"1 0 0 0 1 0 0 0 1 {component['name']}.ldr")
        lines.append("0 STEP")

        measure = component["measure"]
        placements.append({
            "name": component["name"],
            "file": str(component["path"]),
            "at": [_n(x), _n(y), _n(z)],
            "placed_by": "you" if given else "layout",
            "size_studs": measure.get("size_studs"),
            "parts": measure.get("parts"),
        })

    for component in resolved:
        for name, body in namespace(component["text"], component["name"],
                                    f"{component['name']}.ldr"):
            described, body = _description(_strip_meta(body), component["name"])
            lines.append("")
            lines.append(f"0 FILE {name}")
            lines.append(described)
            lines.append(HEADER.format(name=name).rstrip())
            lines.extend(body)

    report = {
        "components": placements,
        "note": ("Components with placed_by='layout' were positioned "
                 "automatically: laid in a row along x, dropped onto y = 0, "
                 "snapped to the stud grid. Pass x/y/z on a component to "
                 "place it yourself."),
    }
    return "\n".join(lines) + "\n", report


def _n(value):
    """A coordinate, integral where it can be - LDraw is read by people too."""
    value = float(value)
    return int(value) if value == int(value) else round(value, 3)
