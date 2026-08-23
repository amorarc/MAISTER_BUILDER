#!/usr/bin/env python3
"""
Acceptance fixtures for ldr_validator.

    python maister/environment_feedback/test_ldr_validator.py

The last two are the false-positive regression tests and they are the point of
the file. A checker that reports overlaps in a correct model is one the reader
learns to ignore, and once they do, the real overlaps go unread too.
"""

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import ldr_validator as V                                   # noqa: E402
from maister.agent.library import ensure_library_root       # noqa: E402

LIBRARY = str(ensure_library_root() or "")
failures = 0


def model(*lines):
    handle = tempfile.NamedTemporaryFile("w", suffix=".ldr", delete=False,
                                         encoding="utf-8")
    handle.write("0 FILE t.ldr\n" + "".join(l if l.endswith("\n") else l + "\n"
                                            for l in lines))
    handle.close()
    return handle.name


def check(label, got, want):
    global failures
    ok = got == want
    if not ok:
        failures += 1
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
          + ("" if ok else f"  (wanted {want})"))


def codes(report):
    out = {}
    for finding in report["findings"]:
        out[finding["code"]] = out.get(finding["code"], 0) + 1
    return out


def run(label, lines, **kwargs):
    path = model(*lines)
    try:
        report = V.validate(path, LIBRARY, **kwargs)
    finally:
        os.unlink(path)
    print(f"\n{label}")
    print(f"  {report['summary']}")
    return report


# A 2x4 brick is 80 x 24 x 40 LDU; -Y is up, so a brick above sits 24 lower.
BRICK = "3001.dat"

r = run("two bricks correctly stacked",
        [f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 2 0 -24 0 1 0 0 0 1 0 0 0 1 {BRICK}"])
check("components", r["summary"]["components"], 1)
check("overlaps", r["summary"]["overlaps"], 0)
check("has a validated connection", r["summary"]["connections"] >= 1, True)

r = run("the same brick placed twice",
        [f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}"])
check("one DUPLICATE", codes(r).get("DUPLICATE"), 1)
check("and no OVERLAP", codes(r).get("OVERLAP", 0), 0)

# A 2x4 brick is 80 LDU long, so a second one 70 along shares 10 LDU of it.
r = run("two bricks overlapping by 10 LDU",
        [f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 2 70 0 0 1 0 0 0 1 0 0 0 1 {BRICK}"])
check("an OVERLAP is reported", codes(r).get("OVERLAP", 0) >= 1, True)
deep = max((f["detail"]["penetration_ldu"] for f in r["findings"]
            if f["code"] == "OVERLAP"), default=0)
check("the move that separates them is about 10 LDU", 8.0 <= deep <= 12.0, True)

# Resting on the brick below but 3 LDU along, so it is in the contact band
# with nothing mating: the studs miss.
r = run("a brick 3 LDU off any valid stud position",
        [f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 2 3 -24 0 1 0 0 0 1 0 0 0 1 {BRICK}"])
check("OFF_LATTICE", codes(r).get("OFF_LATTICE", 0) >= 1, True)
check("TOUCHING_ONLY, not connected", codes(r).get("TOUCHING_ONLY", 0) >= 1, True)
check("no validated connection", r["summary"]["connections"], 0)

r = run("a brick 100 LDU away from the model",
        [f"1 4 0 0 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 2 0 -24 0 1 0 0 0 1 0 0 0 1 {BRICK}",
         f"1 1 400 0 0 1 0 0 0 1 0 0 0 1 {BRICK}"])
check("FLOATING_PART", codes(r).get("FLOATING_PART", 0), 1)
check("two components", r["summary"]["components"], 2)

stack = [f"1 4 0 {-24 * n} 0 1 0 0 0 1 0 0 0 1 {BRICK}" for n in range(10)]
away = [f"1 2 600 {-24 * n} 0 1 0 0 0 1 0 0 0 1 {BRICK}" for n in range(10)]
r = run("a valid subassembly standing well away", stack + away)
check("FLOATING_SUBASSEMBLY", codes(r).get("FLOATING_SUBASSEMBLY", 0), 1)
check("two components", r["summary"]["components"], 2)
check("no overlaps", r["summary"]["overlaps"], 0)

r = run("a part with a negative determinant",
        [f"1 4 0 0 0 -1 0 0 0 1 0 0 0 1 {BRICK}"])
check("MIRRORED", codes(r).get("MIRRORED", 0), 1)

# --- the two that matter -------------------------------------------------
r = run("bricks scaled to 23 LDU tall (a legacy LDraw case)",
        [f"1 4 0 0 0 1 0 0 0 {23 / 24:.6f} 0 0 0 1 {BRICK}",
         f"1 2 0 -23 0 1 0 0 0 {23 / 24:.6f} 0 0 0 1 {BRICK}"])
check("NON_RIGID is reported", codes(r).get("NON_RIGID", 0) >= 1, True)
check("and it is INFO, not an error",
      all(f["severity"] == "info" for f in r["findings"]
          if f["code"] == "NON_RIGID"), True)
check("ZERO overlaps", r["summary"]["overlaps"], 0)

# Round parts at a legal pitch: any overlap here is faceting, not geometry.
ROUND = "3062b.dat"   # Brick 1 x 1 Round
r = run("a field of round bricks at a legal pitch",
        [f"1 4 {x} 0 {z} 1 0 0 0 1 0 0 0 1 {ROUND}"
         for x in (0, 20, 40) for z in (0, 20, 40)])
check("ZERO overlaps from faceting", r["summary"]["overlaps"], 0)

print("\n" + ("FAILED (%d)" % failures if failures else "PASS"))
sys.exit(1 if failures else 0)
