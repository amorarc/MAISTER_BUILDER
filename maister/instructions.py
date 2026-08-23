"""Building instructions for a model, as a booklet you can read on a laptop.

The heavy lifting is LPub3D's: given an LDraw model it lays out one page per
build step, with the step number, a parts list for that step and a render of
the assembly so far. This module is what stands between a model the agent wrote
and a document that is worth opening:

* **Steps.** LPub3D pages a model by its ``0 STEP`` markers, and a model with
  none is one page showing the finished thing — which is not an instruction. So
  a model without steps gets them, split evenly rather than in fives with a
  single brick left over on the last page. A model that *does* have them keeps
  every one, and gets more inside the long ones: the agent writes a handful of
  markers for a hundred parts, and a page that adds sixteen bricks at once is
  not a page anyone can build from.
* **A page shaped like a screen.** The default is A4 portrait for printing.
  These are read on the laptop they were made on, so the page is 16:9 landscape
  and the assembly is scaled up to use it.
* **Somewhere to work.** LPub3D writes its render cache, its logs and its
  exports next to the model file it is given, and none of those paths can be
  configured. It is always handed a copy in a scratch directory.

Requires LPub3D on PATH (see simulator/README.md) and a real X display: it
bundles only the xcb Qt platform plugin, so there is no offscreen mode.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from io import BytesIO
from math import ceil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = PROJECT_ROOT / "app" / "frontend" / "public" / "fonts"

# The app's own identity, so a booklet is recognisably from this workbench.
BRAND = ("MAISTER", "BUILDER")
BRICK_COLOURS = ("#E3000B", "#F6C700", "#00963E", "#0B5FBE")  # red yellow green blue
INK = "#17191E"
# The wordmark's yellow is for dark chrome; on a white page it is barely there,
# so BUILDER takes the bevel shade of the same yellow instead.
GOLD = "#C69E00"

# 16:9 — the shape of the screen these are read on.
PAGE_INCHES = (10.6667, 6.0)

# The assembly is the point of the page, so it is drawn well above LPub3D's
# default scale of 1; the parts list is a reference and stays smaller.
ASSEMBLY_SCALE = 2.5
PARTS_LIST_SCALE = 1.5

# ...but 2.5 is right for a model that fits on the page and wrong for one that
# does not, and one number cannot be both.
#
# LPub3D sizes each step's assembly image from the model's own extent and then
# applies MODEL_SCALE on top, so the picture grows with the model. Across the
# project models on disk the extent spans 60 to 1,940 LDU — 32x — and at a flat
# 2.5 the big end runs clear off all four edges of the page: a 1,940 LDU deck
# renders as a red close-up with about a third of the model visible and the
# parts the step is adding somewhere outside the paper. That is a picture of a
# result rather than an instruction, which is the one thing a booklet must not
# be.
#
# So the scale is capped by how much page there is. Measured rather than
# reasoned about, by rendering one page of that deck at a sequence of scales:
#
#     2.5    about a third of the model on the page
#     1.3    just fits, touching the margins
#     1.0    sits inside them with room  (verified by eye, see the docstring)
#
# 1.3 x 1940 ~ 2500 is therefore the largest "extent x scale" this page holds,
# and that product is the whole of what FIT_LDU says.
#
# Being a cap, it can only ever zoom out: the crossover is at 2500/2.5 = 1000
# LDU, so every model below that — which is 80 of the 83 on disk — is rendered
# exactly as it was before.
FIT_LDU = float(os.environ.get("LDRAW_INSTRUCTIONS_FIT_LDU", "2500"))
# The floor, because the failure has two ends. A model ten thousand LDU across
# would scale to a stamp in the middle of a white page, and a booklet nobody
# can see the parts in is no better than one that overflows.
MIN_ASSEMBLY_SCALE = 0.55

# --------------------------------------------------------------------------
# Where this step's parts go
#
# A step's picture is the whole assembly so far, and by page forty that is a
# hundred bricks with two new ones somewhere in it. Which two is the entire
# information content of the page, and until now the reader had to find them by
# comparing against the previous page — which is the puzzle real instructions
# are designed to remove.
#
# LPub3D answers it two ways at once, and both are worth having because they
# fail in opposite places:
#
# * **Fading what came before.** Everything from earlier steps is washed out,
#   so the new parts are the only things at full strength. This is what LEGO's
#   own booklets do, and it reads instantly — but it says nothing when the new
#   part is hidden behind the model.
# * **Outlining what is new.** The parts this step adds get a coloured edge.
#   That survives being the same colour as its neighbours, which fading does
#   not: two red plates onto a red wall are invisible by fade alone.
#
# 40% rather than LPub3D's default 50: at 50 a pale model — white, tan, light
# grey — fades to something still close enough to full strength that the new
# part does not separate from it.
FADE_OPACITY = int(os.environ.get("LDRAW_INSTRUCTIONS_FADE", "40"))

# ...and it is OFF by default, which is not what this section wanted to say.
#
# The metas below are right and LPub3D accepts them — it logs "Highlight Step
# is ON - Set from meta command" and starts work. What it starts is the
# problem: turning either half on makes it build recoloured copies of the
# whole parts library first, and the log sits on
#
#     Processing Child Color Parts - Count: 23630
#
# On this machine that had not finished after twenty-five minutes, against a
# booklet that renders in about one. Both halves do it — fade needs a washed-out
# copy of every part and highlight an outlined one — so neither is usable on
# its own, and it is a per-process cost, which the parallel renderer in this
# file would pay once per worker.
#
# So it is a switch rather than a default. `LDRAW_INSTRUCTIONS_HIGHLIGHT_STEPS=1`
# turns it on for anyone whose library preparation completes, and the booklet
# keeps working for everyone else. What would make it a default is warming that
# library once, outside the render path, and confirming the cost is paid once
# rather than per run — which is a piece of work, not a constant.
HIGHLIGHT_STEPS = os.environ.get(
    "LDRAW_INSTRUCTIONS_HIGHLIGHT_STEPS", "0") not in ("0", "false", "no")
# Magenta. The meta takes #RRGGBB — the #AARRGGBB the command-line flag
# documents is that flag's format, not this one. Chosen for not being a colour
# LEGO makes bricks in: a yellow outline is invisible on the yellow brick it is
# drawn around, and this whole feature exists for the case where the new part
# matches what it is going onto.
HIGHLIGHT_COLOUR = os.environ.get("LDRAW_INSTRUCTIONS_HIGHLIGHT", "#FF00FF")
# There is no line-width setting here: LPub3D's `-hw` is documented "Enabled
# for LDGlite renderer only" and this booklet is rendered by the native one.

# The most parts one step may add. Two, because that is what a step is for:
# you find the pieces, you see where they go, you turn the page. It was five,
# which reads fine on a model that came with its own build order and badly on
# everything this workbench produces — the agent writes very few markers of its
# own, so a step held whatever was between them, and eleven bricks appearing at
# once on one page is a picture of a result rather than an instruction.
#
# The cost is pages, and pages are free here: this is a PDF nobody prints.
STEP_SIZE = 2

# The page resolution, and the one number that decides how sharp a booklet is.
#
# LPub3D renders each step's assembly to a raster sized from the page, so the
# DPI is not a print setting here — it is the pixel budget every picture in the
# document is drawn with. At LPub3D's default of 150 the page is 1600x900 and a
# step assembly comes out around 410x330: perfectly legible as a thumbnail, and
# visibly soft the moment anyone looks at it on a laptop. At 300 the same
# assembly is drawn with four times the pixels.
#
# It costs render time roughly in proportion to the area, which for a model of
# ordinary size is seconds. Set LDRAW_INSTRUCTIONS_DPI to trade back.
DPI = int(os.environ.get("LDRAW_INSTRUCTIONS_DPI", "300"))
# The cover is composed here rather than by LPub3D, so it has to be built at
# exactly the page resolution or the booklet changes shape halfway through.
COVER_PIXELS = (round(PAGE_INCHES[0] * DPI), round(PAGE_INCHES[1] * DPI))
# Rendered larger than it is placed, so it stays sharp when scaled down —
# capped, because past about 4k a canvas costs real memory for a difference
# nobody can see.
COVER_RENDER = (min(4096, round(COVER_PIXELS[0] * 1.25)),
                min(2304, round(COVER_PIXELS[1] * 1.25)))
# Anti-aliasing samples. LeoCAD and LPub3D's native renderer both take this,
# and at 1 every stud is a staircase. 8 is the highest either accepts.
AA_SAMPLES = int(os.environ.get("LDRAW_INSTRUCTIONS_AA", "8"))
RENDER_TIMEOUT = 120

# LPub3D renders every page before it writes anything, so a big model is a long
# wait rather than a hang. This is the point at which something is wrong.
#
# Raised with STEP_SIZE. A page is a render, so halving the parts per step
# roughly doubles the pages and the time with them: a 180-part model that used
# to be eight pages is ninety, and the old ten minutes was a limit it would
# reach while working correctly. It applies per worker below, and a worker only
# ever holds a slice of the document.
TIMEOUT = 1800

# --------------------------------------------------------------------------
# Rendering the pages at the same time
#
# LPub3D draws one page at a time in one process, and a page is a render of the
# whole assembly so far — so a booklet costs roughly a second a page plus three
# for the program to start. At two parts per step that is ninety seconds for a
# 180-part model and it grows with every model that gets bigger.
#
# It takes `-r 1,2,9,10-42`, a page range, which is the whole trick: the
# document is cut into contiguous slices, a process renders each, and pdfunite
# puts them back in order. Nothing is shared between them and the arithmetic is
# identical, so the pages come out the same as they would have singly.
#
# Two things this depends on, both learned the hard way:
#
# * **The page count has to be known before anything starts.** A range that
#   runs past the end does not stop at the end — asked for 5-9999 of an
#   18-page document LPub3D rendered 9,995 blank pages, and took a minute and a
#   half doing it. Hence `page_count`, and hence `_drop_empty_steps` above,
#   without which the count is wrong by however many empty steps there were.
# * **Each worker needs its own directory.** LPub3D writes its render cache,
#   its logs and its exports beside the model file it was given and none of
#   those paths can be configured, so two workers on one file would be two
#   processes writing one cache.
# --------------------------------------------------------------------------

# 0 means "decide from the machine". One process per core up to the cap, and
# the cap is low on purpose — it was measured, not guessed. On a 16-core
# machine, a 178-part model at 92 pages:
#
#     1 worker    96.0s
#     4 workers   32.2s   3.0x
#     8 workers   36.2s   2.7x
#
# Eight is *slower* than four. The work is not CPU-bound past that point: every
# worker loads its own copy of the parts library and writes its own render
# cache, so the disc and the memory are what run out, and adding processes only
# makes them queue. Raise it with the environment variable on a machine where
# that is not true.
WORKERS = int(os.environ.get("LDRAW_INSTRUCTIONS_WORKERS", "0"))
MAX_WORKERS = 4

# Below this, one process wins. Each worker pays LPub3D's startup — about three
# seconds, and it loads the parts library again — so splitting a six-page
# booklet spends more on starting than it saves on drawing.
MIN_PAGES_TO_SPLIT = 10
# And no worker takes fewer than this, so a 12-page booklet is two processes
# rather than eight of two pages each.
MIN_PAGES_PER_WORKER = 4

_PART = re.compile(r"^\s*1\s")
_FILE = re.compile(r"^\s*0\s+FILE\s", re.IGNORECASE)
_STEP = re.compile(r"^\s*0\s+STEP\s*$", re.IGNORECASE | re.MULTILINE)


class NotAvailable(RuntimeError):
    """LPub3D is not installed, or has no display to render on."""


def lpub3d_binary():
    """The LPub3D executable, or None if it is not installed."""
    found = shutil.which("lpub3d")
    if found:
        return found
    # the AppImage kept in simulator/, if it was never extracted to ~/.local
    here = Path(__file__).resolve().parents[1] / "simulator"
    images = sorted(here.glob("LPub3D-*.AppImage"))
    return str(images[0]) if images else None


def check_available():
    """Raise NotAvailable with something actionable, or return the binary."""
    binary = lpub3d_binary()
    if not binary:
        raise NotAvailable(
            "LPub3D is not installed. See simulator/README.md — extract the "
            "AppImage in simulator/ and link it onto PATH as 'lpub3d'.")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise NotAvailable(
            "LPub3D needs a display to render pages and there is none "
            "(DISPLAY is unset). Start the app from a desktop session, or run "
            "it under 'xvfb-run -a'.")
    return binary


# -- the model ---------------------------------------------------------------

def has_steps(text):
    return bool(_STEP.search(text or ""))


def _submodels(lines):
    """Index ranges for each ``0 FILE`` block, or the whole file if there are none."""
    starts = [i for i, line in enumerate(lines) if _FILE.match(line)]
    if not starts:
        return [(0, len(lines))]
    bounds = starts + [len(lines)]
    return [(bounds[i], bounds[i + 1]) for i in range(len(starts))]


def _even(parts, step_size):
    """Which of these part lines a ``0 STEP`` goes after, the last included.

    As few steps as the cap allows, then the parts spread evenly over them:
    five parts in steps of two become three and two rather than two, two and a
    page with one brick on it.
    """
    if not parts:
        return []
    count = max(1, ceil(len(parts) / step_size))
    size = ceil(len(parts) / count)
    at = {parts[n] for n in range(size - 1, len(parts), size)}
    at.add(parts[-1])  # a run of parts ends with a step closing it
    return sorted(at)


def add_steps(text, step_size=STEP_SIZE):
    """Split a model into build steps of at most ``step_size`` parts.

    Every ``0 STEP`` the author wrote is kept — none is ever removed, so their
    build order survives exactly. What is added is markers *inside* the steps
    that are too long, and markers throughout a model that has none at all.

    This used to return a model with any steps in it untouched, on the
    reasoning that written steps are build order as someone intended it and
    this cannot improve on them. Both halves of that are true and the
    conclusion was still wrong: the agent writes a marker every ten or twenty
    parts, which is a fine outline of a build and a terrible page of an
    instruction booklet. Splitting inside a step the author already closed
    cannot reorder anything — it only says where to stop and look.
    """
    lines = text.splitlines()
    step_size = max(1, int(step_size or STEP_SIZE))
    breaks = set()  # indices *after* which a step marker goes

    for start, end in _submodels(lines):
        run = []  # part lines since the last marker, or since the submodel began
        for i in range(start, end):
            if _PART.match(lines[i]):
                run.append(i)
            elif _STEP.match(lines[i]):
                # The author closes this run themselves. Everything inside it
                # is ours to split; the closing marker is already written, and
                # adding a second one produces a page with nothing on it.
                breaks.update(_even(run, step_size)[:-1])
                run = []
        breaks.update(_even(run, step_size))

    out = []
    for i, line in enumerate(lines):
        out.append(line)
        if i in breaks:
            out.append("0 STEP")
    return _drop_empty_steps(out)


def _drop_empty_steps(lines):
    """Join up, minus any step marker with no parts in front of it.

    An empty step is a page with nothing added on it, and LPub3D agrees: it
    renders no page at all for one. That silence is the problem — it makes the
    document one page shorter than its step count, and the whole of the
    parallel render below is built on knowing how many pages there are before
    anything is rendered. Two of three sample models had one.

    They come from the source, not from the splitting above: two markers in a
    row, a marker before the first part of a submodel, a marker on a file whose
    author closed the last step and then wrote nothing.
    """
    out = []
    since = 0  # parts placed since the last marker
    for line in lines:
        if _PART.match(line):
            since += 1
        elif _FILE.match(line):
            since = 0
        elif _STEP.match(line):
            if not since:
                continue  # a page with nothing on it
            since = 0
        out.append(line)
    return "\n".join(out) + "\n"


def page_count(text):
    """How many pages LPub3D will make of a prepared model.

    One per step, which holds exactly because ``prepare`` leaves no empty ones
    — see ``_drop_empty_steps``. Checked against the renderer in
    ``tools/check_instructions.py``; if it ever drifts, the parallel render
    below notices (it counts what came back) and the pages are still right.
    """
    return len(_STEP.findall(text or ""))


_PLACEMENT = re.compile(r"^\s*1\s+\S+\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)\s")

# What one part adds to the spread between two placements: a placement is an
# origin, and the plastic reaches out around it. Two studs is the ordinary
# brick and it does not have to be exact — this decides a scale, not a fit.
_PART_ALLOWANCE = 40.0


def model_extent(text):
    """Roughly how far across the model is, in LDU.

    Read off the type-1 placements rather than measured from real geometry: the
    answer picks a zoom level, so being a stud out either way changes nothing,
    and paying for the parts library and a bounding box per part to render a
    booklet would not.
    """
    spans = [[], [], []]
    for line in (text or "").splitlines():
        found = _PLACEMENT.match(line)
        if not found:
            continue
        for axis, value in enumerate(found.groups()):
            try:
                spans[axis].append(float(value))
            except ValueError:
                pass
    if not spans[0]:
        return 0.0
    return max(max(a) - min(a) for a in spans) + _PART_ALLOWANCE


def assembly_scale(text):
    """How much to magnify the assembly on the page, for this model.

    ``ASSEMBLY_SCALE`` for anything that fits at it, and the largest scale that
    does fit for anything that does not. See FIT_LDU.
    """
    extent = model_extent(text)
    if extent <= 0:
        return ASSEMBLY_SCALE
    return max(MIN_ASSEMBLY_SCALE, min(ASSEMBLY_SCALE, FIT_LDU / extent))


def _highlight_metas():
    """Fade what came before and outline what is new, when it is switched on.

    Written as metas rather than passed as `-fs`/`-hs` on the command line,
    which is what these look like they should be and is not: the flags parse,
    and then the run logs "Fade Steps is OFF" and renders exactly as before.
    Only the file turns them on.

    `SETUP` must come before `ENABLED` and both must be GLOBAL in the header of
    the top model, or LPub3D discards the pair with "FADE_STEPS SETUP must
    precede FADE_STEPS ENABLED" — which is why each setting is written before
    the ENABLED that closes its group. See HIGHLIGHT_STEPS for why this is off.
    """
    if not HIGHLIGHT_STEPS:
        return []
    return [
        "0 !LPUB FADE_STEPS SETUP GLOBAL TRUE",
        f"0 !LPUB FADE_STEPS OPACITY GLOBAL {FADE_OPACITY:d}",
        # LPub3D fades by writing recoloured copies of the parts itself; the
        # native renderer does not do it on its own.
        "0 !LPUB FADE_STEPS LPUB_FADE GLOBAL TRUE",
        "0 !LPUB FADE_STEPS ENABLED GLOBAL TRUE",
        "0 !LPUB HIGHLIGHT_STEP SETUP GLOBAL TRUE",
        f'0 !LPUB HIGHLIGHT_STEP COLOR GLOBAL "{HIGHLIGHT_COLOUR}"',
        "0 !LPUB HIGHLIGHT_STEP ENABLED GLOBAL TRUE",
    ]


def _metas(scale=None):
    width, height = PAGE_INCHES
    return [
        # LPub3D's default page background is pink, and only a meta changes it
        '0 !LPUB PAGE BACKGROUND COLOR "#FFFFFF"',
        # Before PAGE SIZE, which is given in inches and so is read against
        # whatever resolution is in force at the time.
        f"0 !LPUB RESOLUTION {DPI:g} DPI",
        f"0 !LPUB PAGE SIZE {width:g} {height:g}",
        f"0 !LPUB ASSEM MODEL_SCALE GLOBAL "
        f"{(ASSEMBLY_SCALE if scale is None else scale):g}",
        f"0 !LPUB PLI MODEL_SCALE GLOBAL {PARTS_LIST_SCALE:g}",
        *_highlight_metas(),
    ]


def prepare(text, step_size=STEP_SIZE, scale=None):
    """A model ready for LPub3D: page metas at the top, steps throughout.

    ``scale`` overrides the assembly magnification; None sizes it to the model
    so a big one is drawn from further back — see ``assembly_scale``.
    """
    body = add_steps(text, step_size).splitlines()
    if not body:
        raise ValueError("the model is empty")
    # After the opening 0 FILE line, so the metas belong to the main model
    # rather than to whatever submodel would otherwise claim them.
    at = 1 if _FILE.match(body[0]) else 0
    fitted = assembly_scale(text) if scale is None else scale
    return "\n".join(body[:at] + _metas(fitted) + body[at:]) + "\n"


# -- the cover ---------------------------------------------------------------

@lru_cache(maxsize=8)
def _truetype(filename):
    """One of the app's webfonts as TrueType bytes, or None.

    The app ships woff2, which FreeType cannot open, so the face is converted
    in memory — once per face, since fitting a title to the page asks for a
    dozen sizes of it.
    """
    source = FONTS_DIR / filename
    if not source.is_file():
        return None
    try:
        from fontTools.ttLib import TTFont

        face = TTFont(str(source))
        face.flavor = None  # drop the woff2 wrapper, keep the glyphs
        buffer = BytesIO()
        face.save(buffer)
        return buffer.getvalue()
    except Exception:
        return None


def _font(filename, size):
    """A Pillow font from one of the app's webfonts, or the system's best.

    Worth the trouble of the conversion: the cover carries the same wordmark
    as the toolbar the model was built in.
    """
    from PIL import ImageFont

    data = _truetype(filename)
    if data is not None:
        try:
            return ImageFont.truetype(BytesIO(data), size)
        except OSError:
            pass

    for fallback in ("DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(fallback, size)
        except OSError:
            continue
    return ImageFont.load_default()


def logo_image(path, height=132):
    """Draw the Maister Builder mark and wordmark to a PNG.

    A 2x2 brick seen from above in the four System colours, studs and all,
    with the wordmark beside it — the same mark the app wears, redrawn at
    print size rather than scaled up from a favicon.
    """
    from PIL import Image, ImageDraw

    scale = 4  # drawn large and reduced, since Pillow has no antialiased shapes
    mark = int(height * 0.78)
    pad = int(height * 0.11)
    gap = int(height * 0.22)

    font = _font("bricolage-grotesque-800.woff2", int(height * 0.46))
    canvas = Image.new("RGBA", (10, 10))
    measure = ImageDraw.Draw(canvas)
    first = measure.textlength(BRAND[0] + " ", font=font)
    second = measure.textlength(BRAND[1], font=font)

    width = pad + mark + gap + int(first + second) + pad
    image = Image.new("RGBA", ((width) * scale, (height) * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # the brick: four quarters, a stud on each, a bevel along the bottom
    box = mark * scale
    left, top = pad * scale, int((height - mark) / 2) * scale
    radius = int(box * 0.21)
    draw.rounded_rectangle([left, top, left + box, top + box], radius=radius,
                           fill=BRICK_COLOURS[0])
    half = box // 2
    quarters = [(0, 0), (half, 0), (0, half), (half, half)]
    for (dx, dy), colour in zip(quarters, BRICK_COLOURS):
        quarter = Image.new("RGBA", (half, half), colour)
        rounded = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        rounded.paste(quarter, (dx, dy))
        mask = Image.new("L", (box, box), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, box - 1, box - 1],
                                               radius=radius, fill=255)
        image.paste(rounded, (left, top), Image.composite(
            mask, Image.new("L", (box, box), 0),
            rounded.split()[3]))
    for dx, dy in quarters:
        cx, cy = left + dx + half // 2, top + dy + half // 2
        r = int(box * 0.105)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 128))
    draw.rounded_rectangle([left, top, left + box, top + box], radius=radius,
                           outline=(0, 0, 0, 40), width=max(1, scale))

    text_x = (pad + mark + gap) * scale
    baseline = int((height - height * 0.46) / 2) * scale
    draw.text((text_x, baseline), BRAND[0] + " ", font=_font(
        "bricolage-grotesque-800.woff2", int(height * 0.46) * scale), fill=INK)
    draw.text((text_x + first * scale, baseline), BRAND[1], font=_font(
        "bricolage-grotesque-800.woff2", int(height * 0.46) * scale), fill=GOLD)

    image = image.resize((width, height), Image.LANCZOS)
    path = Path(path)
    image.save(path)
    return path


def render_model(model_text, target, size=None):
    """A picture of the finished model, rendered by LeoCAD.

    LeoCAD rather than LPub3D because it renders with no display at all, and
    because this is one picture of the whole thing rather than a paged
    document. It is given the model *without* step markers, so what it draws is
    the finished build and not the first step of it.
    """
    leocad = shutil.which("leocad")
    if not leocad:
        here = PROJECT_ROOT / "simulator"
        images = sorted(here.glob("LeoCAD-*.AppImage"))
        if not images:
            raise NotAvailable("LeoCAD is not installed; see simulator/README.md")
        leocad = str(images[0])

    target = Path(target).resolve()
    width, height = size or COVER_RENDER
    source = target.with_suffix(".ldr")
    source.write_text(model_text, encoding="utf-8")

    subprocess.run(
        [leocad, str(source), "--image", str(target),
         "-w", str(width), "-h", str(height), "--viewpoint", "home",
         # The cover is the largest picture in the document and was the only
         # one rendered with no anti-aliasing at all.
         "--aa-samples", str(AA_SAMPLES), "--shading", "full"],
        capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    if not target.is_file():
        raise NotAvailable("LeoCAD rendered no image of the model")
    return target


def _fitted(draw, text, filename, width, start, floor=44):
    """The largest size at which `text` fits `width`, and the font for it."""
    size = start
    while size > floor:
        font = _font(filename, size)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 4
    return _font(filename, floor)


def cover_page(title, model_text, target, work_dir):
    """The front page: the finished model, its name, and whose workbench it is.

    Composed here rather than by LPub3D, whose INSERT COVER_PAGE meta quietly
    exports nothing at all in console mode — it wants the GUI. Drawing it is
    also the only way to put things exactly where they belong: the name at the
    top, the model filling the middle, the mark in the bottom-left corner.
    """
    from PIL import Image, ImageDraw

    width, height = COVER_PIXELS
    margin = int(height * 0.075)
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)

    name = (str(title or "").strip() or "model").upper()
    font = _fitted(draw, name, "bricolage-grotesque-800.woff2",
                   width - 2 * margin, int(height * 0.13))
    draw.text((margin, margin), name, font=font, fill=INK)
    top = margin + int(font.size * 1.28)

    # a yellow rule under the name, the one the app draws under its own headings
    draw.rectangle([margin, top, margin + int(width * 0.11), top + max(3, height // 180)],
                   fill=BRICK_COLOURS[1])
    top += int(height * 0.045)

    logo = logo_image(Path(work_dir) / "maister-builder.png",
                      height=int(height * 0.075))
    logo_image_data = Image.open(logo)
    floor = height - margin - logo_image_data.height - int(height * 0.03)

    try:
        render = Image.open(render_model(model_text, Path(work_dir) / "cover-model.png"))
        # LeoCAD frames the model inside the canvas it was given, so most of
        # that canvas is empty. Trimming it lets the model fill the cover
        # instead of floating in the middle of it.
        render = render.crop(render.getbbox() or render.size)
        render.thumbnail((width - 2 * margin, max(1, floor - top)), Image.LANCZOS)
        # centred in the space between the rule and the mark
        page.paste(render, ((width - render.width) // 2,
                            top + max(0, (floor - top - render.height) // 2)),
                   render if render.mode == "RGBA" else None)
    except (NotAvailable, subprocess.TimeoutExpired, OSError) as exc:
        print(f"instructions: no model render on the cover ({exc})", file=sys.stderr)

    page.paste(logo_image_data,
               (margin, height - margin - logo_image_data.height),
               logo_image_data if logo_image_data.mode == "RGBA" else None)

    target = Path(target).resolve()
    page.save(target, "PDF", resolution=float(DPI))
    return target


def _join(cover, booklet, target):
    """Put the cover in front of the booklet."""
    merger = shutil.which("pdfunite")
    if not merger:
        raise NotAvailable("pdfunite (poppler-utils) is needed to attach the cover")
    done = subprocess.run([merger, str(cover), str(booklet), str(target)],
                          capture_output=True, text=True, timeout=120)
    if not Path(target).is_file():
        raise NotAvailable(f"could not attach the cover: {done.stderr.strip()[:200]}")
    return Path(target)


# -- the booklet -------------------------------------------------------------

def _worker_count(pages, asked=None):
    """How many processes to render ``pages`` with."""
    if asked is not None:
        wanted = max(1, int(asked))
    elif WORKERS > 0:
        wanted = WORKERS
    else:
        wanted = min(MAX_WORKERS, os.cpu_count() or 4)
    if pages < MIN_PAGES_TO_SPLIT:
        return 1
    return max(1, min(wanted, pages // MIN_PAGES_PER_WORKER))


def _ranges(pages, workers):
    """``[(first, last)]``, contiguous, one per worker, evenly sized."""
    if workers < 2:
        return [(1, pages)]
    each, over = divmod(pages, workers)
    out, first = [], 1
    for i in range(workers):
        last = first + each + (1 if i < over else 0) - 1
        if last >= first:
            out.append((first, last))
        first = last + 1
    return out


def _render_range(binary, source, target, pages=None, timeout=TIMEOUT):
    """One LPub3D run. ``pages`` is ``(first, last)``, or None for all of them.

    ``target`` must be absolute: LPub3D accepts a relative ``-of`` and then
    fails on every page with "Cannot open device for writing", having reported
    no error at all.
    """
    command = [binary, "-ns", "-ll", "-p", "native", "-pe",
               # The native renderer is LeoCAD's, and takes LeoCAD's quality
               # flags: without them every edge in the booklet is aliased and
               # the plastic is flat-shaded.
               "--aa-samples", str(AA_SAMPLES),
               "--shading", "full",
               "-o", "pdf", "-of", str(target)]
    if pages:
        command += ["-r", f"{pages[0]}-{pages[1]}"]
    command.append(str(source))
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "LPUB3D_DISABLE_UPDATE_CHECK": "1"})
    except subprocess.TimeoutExpired:
        raise NotAvailable(
            f"LPub3D did not finish within {timeout}s — the model may be too "
            f"large to page.") from None
    if not Path(target).is_file():
        where = f" (pages {pages[0]}-{pages[1]})" if pages else ""
        raise NotAvailable(f"LPub3D produced no document{where}: {_why(done)}")
    return Path(target)


def _booklet(binary, source, target, pages, timeout=TIMEOUT, workers=None):
    """The stepped pages, rendered — in several processes where that is faster.

    Falls back to one process for anything that goes wrong with the split. A
    booklet rendered slowly is the outcome this had before; a booklet with
    pages missing is not an outcome at all.
    """
    count = _worker_count(pages, workers) if pages else 1
    if count < 2:
        return _render_range(binary, source, target, timeout=timeout)

    root = Path(target).parent
    try:
        return _in_parallel(binary, source, target, root,
                            _ranges(pages, count), timeout)
    except NotAvailable:
        raise
    except Exception as exc:
        print(f"instructions: rendering in {count} processes failed ({exc}); "
              f"falling back to one", file=sys.stderr)
        return _render_range(binary, source, target, timeout=timeout)


def _in_parallel(binary, source, target, root, ranges, timeout):
    """Render each range in its own process and directory, then join them."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for index, (first, last) in enumerate(ranges):
        # Its own directory, because LPub3D writes its cache, its logs and its
        # exports beside the model file and none of those paths can be set.
        # Its own copy of the source for the same reason.
        folder = root / f"pages-{index:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / Path(source).name
        shutil.copyfile(source, copy)
        jobs.append((copy, (folder / "part.pdf").resolve(), (first, last)))

    # Threads rather than processes: every one of these is waiting on a
    # subprocess, which holds no lock of ours.
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        list(pool.map(
            lambda job: _render_range(binary, job[0], job[1], job[2], timeout),
            jobs))

    parts = [job[1] for job in jobs]
    _unite(parts, target)
    return Path(target)


def _unite(parts, target):
    """Join rendered slices into one document, in page order."""
    if len(parts) == 1:
        Path(parts[0]).replace(target)
        return Path(target)
    merger = shutil.which("pdfunite")
    if not merger:
        raise NotAvailable(
            "pdfunite (poppler-utils) is needed to join the rendered pages")
    subprocess.run([merger, *[str(p) for p in parts], str(target)],
                   capture_output=True, text=True, timeout=300)
    if not Path(target).is_file():
        raise NotAvailable("the rendered pages could not be joined")
    return Path(target)


def build(model_text, name="model", work_dir=None, step_size=STEP_SIZE,
          timeout=TIMEOUT, cover=True, workers=None):
    """Render ``model_text`` into an instruction PDF. Returns its path.

    The PDF is written inside ``work_dir`` (a temporary directory when none is
    given, which the caller then owns and should delete once it has served the
    file).

    ``workers`` is how many LPub3D processes share the pages out; None takes it
    from the machine, 1 renders the whole document in one process as this used
    to. ``timeout`` is per worker.
    """
    binary = check_available()

    # Absolute, always: LPub3D accepts a relative -of and then fails on every
    # page with "Cannot open device for writing", having reported no error.
    root = Path(work_dir or tempfile.mkdtemp(prefix="instructions-")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(name)

    source = root / f"{stem}.mpd"
    source.write_text(prepare(model_text, step_size), encoding="utf-8")
    target = root / f"{stem}.pdf"

    booklet = _booklet(binary, source, root / f"{stem}-steps.pdf",
                       page_count(source.read_text(encoding="utf-8")),
                       timeout=timeout, workers=workers)

    if not cover:
        booklet.replace(target)
        return target

    # A booklet without its cover is still the instructions, so a cover that
    # cannot be drawn costs the cover and not the download.
    try:
        front = cover_page(name, model_text, root / "cover.pdf", root)
        return _join(front, booklet, target)
    except Exception as exc:
        print(f"instructions: no cover page ({exc})", file=sys.stderr)
        booklet.replace(target)
        return target


def _safe_stem(name):
    """A filename from a project name, since the name reaches the filesystem."""
    stem = re.sub(r"[^\w\- ]+", "", str(name or "")).strip()
    stem = re.sub(r"\s+", " ", stem)[:60].strip()
    return stem or "instructions"


# LPub3D prefixes each log line with its level, and one INFO line lists every
# level there is — so the level has to be matched where it sits, not anywhere.
_TROUBLE = re.compile(r"^\s*(ERROR|FATAL)\b|process failed|Cannot open device",
                      re.IGNORECASE)


def _why(done):
    """The line from LPub3D's output that explains a failure."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", f"{done.stdout or ''}\n{done.stderr or ''}")
    for line in reversed(text.splitlines()):
        if _TROUBLE.search(line):
            return " ".join(line.split())[:300]
    return f"exit code {done.returncode}"
