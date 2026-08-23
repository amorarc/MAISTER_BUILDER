"""Tool schemas and dispatch for the LDraw model builder agent."""

import json
import re
from pathlib import Path

from . import (arrange, assembly, autofix, blueprint, catalog, creations,
               geometry, palette, reference, render, runstate, sets, trace,
               validation)
from .config import COPY_FROM_SET_ENABLED, OUT_DIR, PROJECT_ROOT

MAX_FILE_BYTES = 2_000_000
# reading a reference model: enough to study a build, not enough to flood context
MAX_REFERENCE_LINES = 400

# How much of a set's source `get_set_details` carries on its own. Enough that
# a small model arrives whole and a large one arrives usefully begun, without
# a metadata lookup costing the context of a four-hundred-line read.
SET_DETAIL_LINES = 120
# Submodels listed by get_set_details. A big set has over a hundred blocks and
# most are a hinge; the point of the index is the assemblies worth reading.
MAX_SUBMODELS = 40
# Questions per ask_vision_model call. The tool is allowed once between writes,
# so this is the whole budget - generous enough that nothing worth asking has
# to be left out, small enough that it stays a prepared list rather than a
# fishing trip.
MAX_QUESTIONS = 6
# Lines of the edited file shown back around each change, so a chain of edits
# does not need a read_model between them.
EDIT_CONTEXT_LINES = 3


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "plan_construction",
            "description": (
                "Draw up the construction plan for a build. Call this FIRST "
                "whenever you are about to create a model - a building, a "
                "vehicle, any new model - and before any substantial change to "
                "an existing one. It researches real LEGO sets for the subject, "
                "then returns the footprint, every Y level in LDU with the "
                "arithmetic that produced it, a bill of materials whose part "
                "numbers are already resolved against the catalogue (with "
                "footprints and place_height_ldu), and the assembly steps in "
                "order. Build the steps it gives you rather than replanning: "
                "one call replaces a dozen part searches. Skip it only for "
                "trivial edits - a colour change, moving or deleting one part."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": (
                            "What is being built, in plain words: 'a small "
                            "two-storey house with a sloped red roof', 'a "
                            "chimney on the existing house'."
                        ),
                    },
                    "requirements": {
                        "type": "string",
                        "description": (
                            "Anything the user asked for specifically - colours, "
                            "features, style, a part they named."
                        ),
                    },
                    "model_path": {
                        "type": "string",
                        "description": (
                            "Path of the model being changed, as used with "
                            "edit_model. Give it whenever the build extends "
                            "something that already exists, so the plan works "
                            "from what is there. Omit for a new model."
                        ),
                    },
                    "max_pieces": {
                        "type": "integer",
                        "description": "Optional piece budget the plan must come in under.",
                    },
                    "footprint_studs": {
                        "type": "string",
                        "description": "Optional size limit in studs, e.g. '8 x 8'.",
                    },
                    "use_references": {
                        "type": "boolean",
                        "description": (
                            "Look up official sets for the subject first. Default "
                            "true; set false for a small change, where the "
                            "research is wasted."
                        ),
                    },
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": (
                "Search the LDraw parts catalogue. Hybrid: exact keyword matching "
                "fused with semantic vector search, so both 'brick 2 x 4' and "
                "'something curved for a car roof' work. Returns matching parts "
                "with their footprint in studs, how they join, and the height to "
                "use when stacking. Always use this instead of guessing a part "
                "number. It answers with three more things: `companion_parts`, "
                "the parts real sets put beside the results (a rim's tyre, a "
                "turntable's other half) - place those too or the assembly is "
                "unfinished; `other_shape_families`, the families the search "
                "reached but had no room to show, to search again with "
                "category= rather than settle for a plain brick; and "
                "`parts_you_have_found`, every part this project has turned up "
                "so far, including in earlier subconstructions. Never search "
                "again for something already on that list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What you are looking for. Either catalogue wording "
                            "('brick 2 x 4', 'plate round') or a plain description "
                            "of the shape or role you need."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter: Brick, Plate, Tile, Slope, Technic, ...",
                    },
                    "width_studs": {"type": "integer", "description": "Optional footprint width in studs."},
                    "depth_studs": {"type": "integer", "description": "Optional footprint depth in studs."},
                    "max_results": {"type": "integer", "description": "Default 12."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_part_details",
            "description": (
                "Exact geometry for one part: bounding box in LDU, footprint in "
                "studs, the stud grid (offsets from the part's origin where other "
                "parts attach), and place_height_ldu - subtract this from the y of "
                "the part below to stack this part on top of it.\n\n"
                "`studs` is the part measured rather than estimated: every stud "
                "on it, at its offset from the part's own origin. `on_top` are "
                "the ones you can build on - a 2x2 slope has two, where its "
                "bounding box implies four. `underside` are the tubes it comes "
                "down over, which are what must land on the studs below. And "
                "`on_the_sides` are studs facing sideways, each with the "
                "rotation matrix that turns a part to go onto it - a headlight "
                "brick, a bracket, a plate with studs on its side. That matrix "
                "is the whole of how a model gets a face with no studs showing, "
                "and it is not derivable from anything else here.\n\n"
                "Also how the "
                "part JOINS to anything: `connections` names the families it "
                "belongs to (stud and tube, clip and bar, Technic pin, axle and "
                "cross-hole, ball and socket, hinge, turntable, gear, track, "
                "SNOT, tyre) and which half of each it offers; `attachment` and "
                "`studs_required` say what has to already be there for it to go "
                "on. Two parts with no family in common cannot be joined, "
                "whatever their coordinates - and validation will not catch it, "
                "so read this before placing a part you have not used before. "
                "`used_with` lists the parts real sets put beside this one and "
                "how often: a wheel rim's tyre at 66%, a turntable's other half "
                "at 93%. A high percentage means the part is half of an "
                "assembly and placing it alone leaves the assembly unfinished."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part_id": {
                        "type": "string",
                        "description": "Part number, with or without .dat (e.g. '3001' or '3001.dat').",
                    }
                },
                "required": ["part_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_reference",
            "description": (
                "Search everything that is not a part: 1,800 real LEGO models "
                "and the models you have built and saved. Search `sets` BEFORE "
                "building anything non-trivial - a real set that already solved "
                "a similar shape is the best reference you can get, and what "
                "comes back can be read with read_model('set:...')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["sets", "creations"],
                        "description": (
                            "sets: official LEGO models. creations: models you "
                            "built and saved earlier."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "What you are after, in plain words - 'a small "
                            "medieval house with a sloped roof'."
                        ),
                    },
                    "like": {
                        "type": "string",
                        "description": (
                            "sets only, instead of a query: a set number to "
                            "find others close to, e.g. '10030-1'. Use it to "
                            "widen a reference you already like, or to see how "
                            "several sets solved one subject differently."
                        ),
                    },
                    "theme": {
                        "type": "string",
                        "description": "sets: Star Wars, Town, Castle, Technic, ...",
                    },
                    "year_min": {"type": "integer", "description": "sets: earliest year."},
                    "year_max": {"type": "integer", "description": "sets: latest year."},
                    "min_pieces": {"type": "integer", "description": "sets/creations."},
                    "max_pieces": {
                        "type": "integer",
                        "description": (
                            "sets/creations. Worth setting - a 3,000-piece "
                            "model is a poor reference for a 40-piece build."
                        ),
                    },
                    "tag": {"type": "string", "description": "creations: filter by tag."},
                    "validated_only": {
                        "type": "boolean",
                        "description": "creations: only ones that passed validation.",
                    },
                    "max_results": {"type": "integer", "description": "Default 8."},
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_model",
            "description": (
                "Read LDraw source with line numbers, wherever the model "
                "lives: the one you are building, an official set, or one of "
                "your own saved creations. Those line numbers are the ones "
                "edit_model takes. Reading the submodel of a real set before "
                "building something similar beats deriving the geometry from "
                "nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": (
                            "What to read. A model path relative to out/, e.g. "
                            "'projects/abc/model.ldr'; an official set as "
                            "'set:10030-1'; one of your saved models as "
                            "'creation:oak tree'."
                        ),
                    },
                    "submodel": {
                        "type": "string",
                        "description": (
                            "One named block of an MPD instead of a line range "
                            "- 'wing.ldr', or any part of that name. This is "
                            "how to read one assembly of a real set: sets are "
                            "built out of submodels, and the one you want is a "
                            "few dozen lines somewhere inside a few thousand. "
                            "get_set_details lists the names."
                        ),
                    },
                    "start_line": {"type": "integer"},
                    "end_line": {
                        "type": "integer",
                        "description": f"Capped at {MAX_REFERENCE_LINES} lines per call.",
                    },
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_set_details",
            "description": (
                "Open an official set and read its LDraw source - the real "
                "coordinates LEGO's own designers used. Comes back with the "
                "start of the file and an index of its submodels, so you can "
                "go straight to the assembly you need with "
                "read_model('set:<number>', submodel='<name>')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "set_number": {
                        "type": "string",
                        "description": "Set number ('10030-1') or model file name.",
                    },
                },
                "required": ["set_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_model",
            "description": (
                "Put lines into a model file - the one tool that writes. An "
                "LDraw file IS a program: one part per line, in order, and "
                "every fault you are told about comes with the line it is on. "
                "So work on it line by line - replace the line with the wrong "
                "coordinate, delete the duplicate part, insert the course of "
                "bricks that is missing.\n\n"
                "A file that does not exist yet is an empty file: to START a "
                "model, insert the whole thing at start_line 1. Paths are "
                "relative to the project's out/ directory.\n\n"
                "Several edits go in ONE call and are applied together. Every "
                "line number refers to the file as it is RIGHT NOW, before any "
                "edit in this call: number them from what read_model, "
                "validate_model or your last write showed you, and do not try "
                "to allow for lines that earlier edits in the same call will "
                "shift. Nothing is written unless every edit is good, so a "
                "call that is refused leaves the model exactly as it was."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "The model file to write, e.g. 'my_model/house.mpd'. "
                            "It is created if it is not there yet."
                        ),
                    },
                    "edits": {
                        "type": "array",
                        "description": (
                            "The changes, in any order. They must not touch "
                            "the same line as each other."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["replace", "insert", "delete"],
                                    "description": (
                                        "replace: swap these lines for new "
                                        "ones. insert: put new lines in before "
                                        "start_line, moving the rest down. "
                                        "delete: take these lines out."
                                    ),
                                },
                                "start_line": {
                                    "type": "integer",
                                    "description": (
                                        "First line this edit acts on, counting "
                                        "from 1. For insert it is the line the "
                                        "new text goes BEFORE - use "
                                        "total_lines + 1 to add at the end."
                                    ),
                                },
                                "end_line": {
                                    "type": "integer",
                                    "description": (
                                        "Last line acted on, inclusive. Defaults "
                                        "to start_line, which is one line. Not "
                                        "used by insert."
                                    ),
                                },
                                "lines": {
                                    "type": "string",
                                    "description": (
                                        "The new text for replace and insert, "
                                        "one LDraw line per newline. Not used "
                                        "by delete."
                                    ),
                                },
                                "expect": {
                                    "type": "string",
                                    "description": (
                                        "REQUIRED for replace and delete: the "
                                        "text currently on start_line, copied "
                                        "as it is. The edit is refused if it "
                                        "does not match, which is what stops a "
                                        "stale line number from quietly "
                                        "deleting the wrong brick. Leading and "
                                        "trailing spaces are ignored."
                                    ),
                                },
                            },
                            "required": ["op", "start_line"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_ops",
            "description": (
                "Build by describing the arrangement instead of typing "
                "coordinates. **You give the position of the FIRST part only; "
                "the rest are worked out from the part's real size.**\n\n"
                "  row   - n in a line:   {\"op\":\"row\",\"part\":\"3941\","
                "\"colour\":2,\"at\":[0,-216,0],\"count\":5}\n"
                "  grid  - n by m:        {\"op\":\"grid\",\"part\":\"3024\","
                "\"colour\":71,\"at\":[0,0,0],\"counts\":[6,8]}\n"
                "  stack - n upward:      {\"op\":\"stack\",\"part\":\"3062b\","
                "\"colour\":70,\"at\":[0,-24,0],\"count\":6}\n"
                "  ring  - 4 round a centre, each turned to face out: "
                "{\"op\":\"ring\",\"part\":\"4286\",\"colour\":70,"
                "\"at\":[0,-240,0],\"count\":4,\"radius_ldu\":20}\n"
                "  mirror- a symmetric pair: {\"op\":\"mirror\",\"part\":"
                "\"3039\",\"colour\":4,\"at\":[60,-24,0],\"about\":\"x\"}\n"
                "  wall  - bonded course-work, bricks chosen for you: "
                "{\"op\":\"wall\",\"colour\":4,\"at\":[0,0,0],"
                "\"axis\":\"x\",\"length_studs\":12,\"courses\":3}\n"
                "  box   - four bonded walls round a rectangle: "
                "{\"op\":\"box\",\"colour\":4,\"at\":[0,0,0],"
                "\"size_studs\":[10,8],\"courses\":4}\n"
                "  fill  - a region tiled with the parts YOU name, bonded: "
                "{\"op\":\"fill\",\"colour\":4,\"at\":[0,0,0],"
                "\"size_studs\":[10,8],\"courses\":3,"
                "\"parts\":[\"3008\",\"3009\",\"3004\"]}\n"
                "  place - a single part: {\"op\":\"place\",\"part\":\"3001\","
                "\"colour\":4,\"at\":[40,-8,0]}\n\n"
                "And four that place nothing themselves - they say what "
                "happens to the ops **inside** them, which is how you say "
                "'and again', 'and the same on the other side', and 'that "
                "thing, six times':\n\n"
                "  repeat  - the ops inside, n times, each moved on from the "
                "last: {\"op\":\"repeat\",\"times\":4,\"step\":[0,-24,0],"
                "\"ops\":[ ... ]}\n"
                "  reflect - the ops inside, and their mirror image: "
                "{\"op\":\"reflect\",\"about\":\"x\",\"plane\":0,"
                "\"ops\":[ ... ]}\n"
                "  define  - name an assembly, build it nowhere: "
                "{\"op\":\"define\",\"name\":\"window\",\"ops\":[ ... ]}\n"
                "  call    - put a defined assembly somewhere: "
                "{\"op\":\"call\",\"name\":\"window\",\"at\":[-60,-48,50],"
                "\"rotate\":90}\n\n"
                "**Reach for the repeating ops first; `place` last.** If you "
                "are about to write two `place` ops for the same part in a "
                "line, that is a `row` with `count: 2`, and it is shorter and "
                "cannot be mis-spaced. `place` is for the odd part that "
                "belongs nowhere in a pattern - a door, a chimney, one tile. A "
                "call that is twenty `place` ops is this tool being used as a "
                "typewriter.\n\n"
                "**Four identical courses is a `repeat`, not four lists of "
                "ops.** This is the single most common shape a build has and "
                "the one most often typed out four times. Write the course "
                "once and repeat it upward:\n"
                "{\"op\":\"repeat\",\"times\":4,\"step\":[0,-24,0],\"ops\":["
                "{\"op\":\"row\",\"part\":\"3010\",\"colour\":4,"
                "\"at\":[0,0,0],\"count\":3}]}\n"
                "`step` is how far each copy moves from the one before it - "
                "[0,-24,0] is one brick course up, [80,0,0] is one 2x4 along. "
                "The copies cannot drift, because there is only one position "
                "written down.\n\n"
                "**Anything symmetric is a `reflect`.** It builds the ops "
                "inside it AND their mirror image on the far side of the "
                "plane, each part turned so the pair reads as a mirror rather "
                "than two copies facing the same way. A wing, an arm, a wheel "
                "arch, a whole side of a building - a detail that lands a stud "
                "off on one side only is the commonest reason a build reads as "
                "unfinished, and by hand that is exactly what happens. "
                "`mirror` does this for ONE part; `reflect` does it for "
                "everything inside it, which is what symmetry usually means.\n\n"
                "**`define` and `call` are for a shape you build more than "
                "once** - a window, a wheel, a battlement, a leg. Define it "
                "once at [0,0,0], then `call` it wherever it goes. `call` "
                "takes `at`, and optionally `rotate` (turns the whole assembly "
                "about Y) and `mirror` (\"x\" or \"z\", for the handed copy). "
                "Definitions last for this one build_ops call.\n\n"
                "**`ring` is for anything that goes round something**: the "
                "slopes finishing a tower roof, four walls about a courtyard. "
                "It turns each part as well as placing it, so they all face "
                "outward - writing that by hand is how four slopes end up a "
                "full stud inside each other. `count` is 2 or 4 (a right "
                "angle); `radius_ldu` defaults to the part's own depth, which "
                "puts four of them edge to edge.\n\n"
                "**`wall`, `box` and `fill` are for anything made of "
                "courses** - a wall, a cube, a room, a tower, a chimney, a "
                "floor, a solid mass, a raised bed. They lay a ladder of "
                "bricks chosen to *bond*: longest that fits, seams broken "
                "course to course and row to row, so no vertical joint runs "
                "through. Never lay a wall as rows of 2x4s. That is the single "
                "most common thing this builder does and it is wrong twice - "
                "every course has a straight vertical joint running through "
                "it, which is where a real wall comes apart, and the whole "
                "thing is one shape repeated, which is what makes a model read "
                "as assembled rather than designed. A bonded 12x4 wall is 10 "
                "parts and 3 shapes; the same wall in 2x4s is 12 parts and 1. "
                "`box` alternates which pair of walls runs through, course by "
                "course, so the corners interlock - and it refuses a "
                "single-course box, which is four walls that touch and are "
                "joined nowhere.\n\n"
                "**`fill` is those two with the bricks left to you.** `wall` "
                "and `box` pick their own, which is why they are worth "
                "reaching for when you do not care; `fill` takes `parts` - the "
                "list to tile the region with - so the palette and the shapes "
                "stay yours while the bonding stays automatic. It also does "
                "the one thing neither of the others does: a **solid** "
                "region. A floor, a slab, a plinth, a mass. "
                "`size_studs` is the region, `courses` how many high, "
                "`hollow: true` makes it a shell instead. Every part in "
                "`parts` must be the same width and the same height - a course "
                "is one course - and each a different length, since that is "
                "what there is to choose between.\n\n"
                "**`mirror` is for anything symmetric**: a pair of wings, two "
                "arms of a chair, headlights. It places the part at `at` and "
                "again on the far side of the `about` plane (\"x\" or \"z\", "
                "through `plane`, default 0), turned so the pair reads as a "
                "mirror image rather than two copies facing the same way. Use "
                "it wherever the model has an axis - a detail that lands a "
                "stud off on one side only is the commonest reason a build "
                "reads as unfinished, and by hand that is exactly what "
                "happens.\n\n"
                "Why the spacing is not yours to give: a 2x2 round brick is 40 "
                "LDU across, so a row of them steps 40 LDU, not 20. That one "
                "slip is the commonest way a model becomes unbuildable, and "
                "here it cannot be written down. Rotating a part turns its "
                "footprint too, so the spacing follows by itself.\n\n"
                "Rules: `colour` is required on every op (an LDraw code - 16 "
                "means 'inherit' and makes parts look uncoloured). `at` is "
                "[x, y, z] in LDU with x and z on multiples of 10, and −Y is "
                "up, so a stack goes to smaller y and this does that "
                "subtraction. `rotate` is degrees about Y - 0, 90, 180 or 270 "
                "- and for anything with a direction to it (a slope, a wedge, "
                "a bracket, a printed tile) which way it faces is part of "
                "placing it, not a refinement afterwards. "
                "`gap_studs` opens a deliberate gap. `note` writes a comment "
                "above the parts.\n\n"
                "If the parts it would place would share solid plastic - with "
                "each other or with the model - **nothing is written at all** "
                "and it names the clash. That is not a failure to work around: "
                "move the `at` that is wrong and call again. The file on disk "
                "is untouched, so there is nothing to undo.\n\n"
                "Use `edit_model` for what this cannot do: moving or deleting "
                "a part already placed, recolouring one line, and any angle "
                "that is not a multiple of 90 (a hinge, a posed limb, a "
                "minifigure). Most builds use both."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Model to build into, relative to out/.",
                    },
                    "ops": {
                        "type": "array",
                        "description": (
                            "The build sequence, in order. Each op is an "
                            "object: {\"op\": \"row\", \"part\": \"3941\", "
                            "\"colour\": 2, \"at\": [40, -216, 0], "
                            "\"count\": 5}."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "enum": ["place", "row", "grid", "stack",
                                             "ring", "mirror", "wall", "box",
                                             "fill", "repeat", "reflect",
                                             "define", "call"],
                                },
                                "part": {
                                    "type": "string",
                                    "description": (
                                        "Part id from the catalogue, no .dat."),
                                },
                                "parts": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "fill: the parts to tile the region "
                                        "with, e.g. ['3008','3009','3004']. "
                                        "All the same width and height, each a "
                                        "different length. Leave it out to use "
                                        "the standard brick ladder."),
                                },
                                "ops": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                    "description": (
                                        "repeat, reflect, define and call: the "
                                        "ops this one applies to. They are "
                                        "ordinary ops and may themselves be "
                                        "groups."),
                                },
                                "times": {
                                    "type": "integer",
                                    "description": (
                                        "repeat: how many copies in total, "
                                        "counting the first."),
                                },
                                "step": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "description": (
                                        "repeat: [dx, dy, dz] in LDU from each "
                                        "copy to the next. [0,-24,0] is one "
                                        "brick course upward. x and z on "
                                        "multiples of 10."),
                                },
                                "name": {
                                    "type": "string",
                                    "description": (
                                        "define: what to call this assembly. "
                                        "call: which one to place."),
                                },
                                "keep": {
                                    "type": "boolean",
                                    "description": (
                                        "reflect: build the near side too. "
                                        "Default true - a reflect is normally "
                                        "'and the same on the other side'."),
                                },
                                "hollow": {
                                    "type": "boolean",
                                    "description": (
                                        "fill: a shell of walls rather than a "
                                        "solid region. Needs 2+ courses."),
                                },
                                "colour": {
                                    "type": "integer",
                                    "description": "LDraw colour code. Required.",
                                },
                                "at": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "description": (
                                        "[x, y, z] in LDU of the first part. "
                                        "x and z on multiples of 10; -Y is up."),
                                },
                                "count": {
                                    "type": "integer",
                                    "description": "row, stack and ring: how many. A ring is 2 or 4.",
                                },
                                "counts": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "grid: [along_x, along_z].",
                                },
                                "radius_ldu": {
                                    "type": "number",
                                    "description": (
                                        "ring: centre-to-part distance, a "
                                        "multiple of 10. Defaults to the "
                                        "part's own depth, which puts four of "
                                        "them edge to edge round a square."),
                                },
                                "about": {
                                    "type": "string",
                                    "enum": ["x", "z"],
                                    "description": (
                                        "mirror and reflect: which plane the "
                                        "pair or the group is symmetric "
                                        "about."),
                                },
                                "mirror": {
                                    "type": "string",
                                    "enum": ["x", "z"],
                                    "description": (
                                        "call: place the handed copy of this "
                                        "assembly, mirrored about that plane."),
                                },
                                "plane": {
                                    "type": "number",
                                    "description": (
                                        "mirror and reflect: where that plane "
                                        "sits, in LDU. Default 0, the model's "
                                        "centre line."),
                                },
                                "axis": {
                                    "type": "string",
                                    "enum": ["x", "z"],
                                    "description": (
                                        "row, wall and fill: which way it "
                                        "runs."),
                                },
                                "rotate": {
                                    "type": "integer",
                                    "description": (
                                        "Degrees about Y: 0, 90, 180 or 270. "
                                        "Which way a slope, a wedge, a bracket "
                                        "or a printed tile faces is a decision "
                                        "- three placements in four in real "
                                        "sets are turned, and a model where "
                                        "every part faces the same way reads "
                                        "as a stack of boxes. The footprint "
                                        "turns with the part, so the spacing "
                                        "follows by itself."),
                                },
                                "gap_studs": {
                                    "type": "number",
                                    "description": (
                                        "A deliberate gap between the parts, "
                                        "in studs. Default 0, meaning they "
                                        "sit flush."),
                                },
                                "length_studs": {
                                    "type": "integer",
                                    "description": (
                                        "wall: how many studs the wall runs "
                                        "for. The bricks that fill it are "
                                        "chosen for you."),
                                },
                                "courses": {
                                    "type": "integer",
                                    "description": (
                                        "wall, box and fill: how many courses "
                                        "high. A box or a hollow fill needs at "
                                        "least 2 - one course is four walls "
                                        "with no stud between them."),
                                },
                                "thickness_studs": {
                                    "type": "integer",
                                    "enum": [1, 2],
                                    "description": (
                                        "wall, box and fill: how thick, in "
                                        "studs, when no `parts` were named. "
                                        "Default 1."),
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["brick", "plate"],
                                    "description": (
                                        "wall, box and fill: what the courses "
                                        "are made of when no `parts` were "
                                        "named. Default brick."),
                                },
                                "size_studs": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": (
                                        "box and fill: [along_x, along_z], the "
                                        "footprint in studs."),
                                },
                                "note": {
                                    "type": "string",
                                    "description": (
                                        "Written above these parts as a "
                                        "comment."),
                                },
                            },
                            # `op` only. `colour` and `at` are required of
                            # every op that places something and the compiler
                            # says so by name when one is missing - but a
                            # `repeat` has neither, and a schema that demanded
                            # them would make the group ops unwritable.
                            "required": ["op"],
                        },
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["append", "replace"],
                        "description": (
                            "`append` adds to what is there (the default). "
                            "`replace` starts the file again from these ops."),
                    },
                    "submodel": {
                        "type": "string",
                        "description": (
                            "Which submodel to add to, for a file that holds "
                            "several. Required for those files."),
                    },
                    "title": {
                        "type": "string",
                        "description": "Model title, when replacing.",
                    },
                    "allow_half_offset": {
                        "type": "boolean",
                        "description": (
                            "Only when the parts are deliberately half a stud "
                            "out of phase with the rest of the model because "
                            "they sit on jumper plates. Without it, a half-stud "
                            "mismatch is refused as the mistake it almost "
                            "always is."),
                    },
                },
                "required": ["path", "ops"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_from_set",
            "description": (
                "Take an assembly out of a real released set and put it in the "
                "model you are building - as real part lines, at your "
                "coordinates, which you then own and can change.\n\n"
                "This is what the reference sets are *for*. Reading a wing's "
                "thirty lines of coordinates and typing them out again shifted "
                "to your origin is expensive and is exactly the arithmetic that "
                "goes wrong; this moves them for you. The assembly arrives "
                "re-anchored so its footprint is centred on `at` and its "
                "underside sits at that height, turned by `rotate`, recoloured "
                "if you ask, and with a comment in the file saying which set "
                "and submodel it came from.\n\n"
                "The workflow: `search_reference(kind=\"sets\")` to find a set "
                "that solved this shape, `get_set_details` to see its "
                "assemblies and their part counts, "
                "`read_model(\"set:<n>\", submodel=\"<name>\")` to look at the "
                "one you want, then this to bring it across.\n\n"
                "**Once it is in, it is yours.** Recolour it, delete the parts "
                "you do not want with `edit_model`, build onto it with "
                "`build_ops`. Grafting a torso and redressing it is the "
                "intended use; grafting a whole set and calling it a build is "
                "not - take assemblies, not models.\n\n"
                "It refuses and writes nothing if the assembly would overlap "
                "what is already there or land on a different stud lattice, "
                "and it never modifies the set."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The model to graft into, relative to out/.",
                    },
                    "set_number": {
                        "type": "string",
                        "description": "The set to take from, e.g. '41590-1'.",
                    },
                    "submodel": {
                        "type": "string",
                        "description": (
                            "Which assembly of that set to take, by name from "
                            "get_set_details. Omit to take the whole model, "
                            "which is rarely what you want."),
                    },
                    "at": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": (
                            "[x, y, z] in LDU where the assembly goes. By "
                            "default that is the middle of its footprint and "
                            "its underside - the point you would set it down "
                            "on."),
                    },
                    "rotate": {
                        "type": "integer",
                        "description": "Degrees about Y, a multiple of 90.",
                    },
                    "recolour": {
                        "type": "object",
                        "description": (
                            "Repaint it. A map of old LDraw colour code to new, "
                            "as {\"4\": 1} to turn its red parts blue. The "
                            "shape is the reusable thing; the set's colours "
                            "usually are not."),
                    },
                    "anchor": {
                        "type": "string",
                        "enum": ["bottom-centre", "centre", "origin"],
                        "description": (
                            "How `at` is read. Default bottom-centre. 'origin' "
                            "keeps the set's own coordinates and just shifts "
                            "by `at`."),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["append", "replace"],
                        "description": "Default append.",
                    },
                    "into_submodel": {
                        "type": "string",
                        "description": (
                            "Which submodel of *your* file to add to, when it "
                            "holds several."),
                    },
                    "only_parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Take only these part numbers out of the assembly "
                            "- the four wheels of a racer without its "
                            "chassis."),
                    },
                    "exclude_parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Take the assembly except these parts.",
                    },
                    "matching": {
                        "type": "string",
                        "description": (
                            "Take only the parts whose catalogue description "
                            "contains this word - 'wheel', 'windscreen', "
                            "'slope'. Combines with the two above."),
                    },
                    "allow_half_offset": {
                        "type": "boolean",
                        "description": (
                            "Only when the assembly is deliberately half a "
                            "stud out of phase with your model."),
                    },
                },
                "required": ["path", "set_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_model",
            "description": (
                "Check the model AND look at it - the two questions that "
                "decide whether a build is finished, in one call. First the "
                "grid: every part on the stud grid, nothing sharing solid "
                "plastic, every part number real; overlaps that are pure "
                "arithmetic are slid back into place for you, and the model's "
                "size comes back under `size`. Then the eyes: it is rendered "
                "from six viewpoints - four corners a quarter turn apart, "
                "plus front and top - and a vision model describes what is "
                "actually there - whether it reads as the thing you were "
                "asked for, what is floating, what is out of proportion. When "
                "the user attached a reference picture the renders are "
                "compared against it too, difference by difference. A model "
                "can pass the grid completely and still not look like what "
                "was asked for, which is why this does both. Call it after "
                "every change.\n\n"
                "**Both halves run every time, and neither can be turned "
                "off.** The model is rendered and looked at even while the "
                "grid check is failing - a build with an overlap in it is "
                "still a build whose shape is worth knowing about, and the "
                "two faults are almost always repaired in the same edit.\n\n"
                "It may also return `style`: your model's part variety, "
                "colours and use of rotation measured against the 1,800 real "
                "sets in the reference corpus, but only when the build is a "
                "long way outside what sets its size look like. That is not a "
                "fault and never fails the model - it is the one signal that "
                "tells you a build is correct and still dull."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Model to check, relative to out/.",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional: something specific you want the vision "
                            "model to check, e.g. 'is the roof attached to "
                            "the walls?'."
                        ),
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Stud-grid tolerance in LDU. Default 2.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_about_image",
            "description": (
                "Look at the reference picture the user attached - you cannot "
                "see it, and this is the only way to know what is in it. "
                "Called with no questions it describes the picture: do that "
                "FIRST, before planning anything. Called with questions it "
                "answers them, which is how you settle what the description "
                "left open instead of inventing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Specific questions about the picture. Omit to get "
                            "a description instead. Group the ones you already "
                            "know you need into one call rather than asking "
                            "them one at a time; come back whenever the "
                            "picture is genuinely what you are missing. Ten "
                            "calls per build."
                        ),
                    },
                    "request": {
                        "type": "string",
                        "description": (
                            "When describing: what to pay attention to, e.g. "
                            "'the colours and how the roof is shaped'."
                        ),
                    },
                    "purpose": {
                        "type": "string",
                        "description": "When asking: what you need the answers for.",
                    },
                    "image_id": {
                        "type": "string",
                        "description": "Which attached picture. Defaults to the latest.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_submodel",
            "description": (
                "Shift one whole subconstruction inside an assembled scene - "
                "the tree, the house, the car - by a distance in LDU. This is "
                "how a scene is arranged: the objects are finished and "
                "correct, and what is wrong is where they are standing "
                "relative to each other. Move on the stud grid: multiples of "
                "20 sideways, 24 for a brick of height, 8 for a plate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The scene file."},
                    "submodel": {
                        "type": "string",
                        "description": (
                            "Which object to move, by name - 'tree', "
                            "'tree.ldr'. The scene lists what it holds."
                        ),
                    },
                    "dx": {"type": "number", "description": "Left/right, in LDU."},
                    "dy": {
                        "type": "number",
                        "description": "Up/down. Remember +Y is DOWN, so up is negative.",
                    },
                    "dz": {"type": "number", "description": "Forward/back, in LDU."},
                },
                "required": ["path", "submodel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rotate_submodel",
            "description": (
                "Turn one whole subconstruction where it stands - to face it "
                "a different way, or to lay it on its side. It turns about "
                "the object's own centre, so it stays where it is instead of "
                "swinging across the scene. Quarter turns only: any other "
                "angle takes every stud in the object off the grid at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The scene file."},
                    "submodel": {"type": "string", "description": "Which object to turn."},
                    "degrees": {
                        "type": "number",
                        "description": "90, 180, 270 or -90. Default 90.",
                    },
                    "axis": {
                        "type": "string",
                        "enum": ["x", "y", "z"],
                        "description": (
                            "y turns it on the spot to face another way, and "
                            "is almost always the one you want. x and z tip it "
                            "over."
                        ),
                    },
                },
                "required": ["path", "submodel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assemble_model",
            "description": (
                "Combine finished subconstruction files into one scene. Each "
                "component becomes a submodel of the result, placed so that "
                "none of them overlap: laid in a row, dropped onto the ground "
                "plane, snapped to the stud grid. Give x/y/z on a component "
                "only when you want it somewhere specific - the computed "
                "layout is measured from real bounding boxes and is usually "
                "right. Do not write the combining MPD by hand; this exists so "
                "that arithmetic is not yours to get wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Where to write the assembled scene.",
                    },
                    "components": {
                        "type": "array",
                        "description": "The subconstructions, in the order they should be laid out.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {
                                    "type": "string",
                                    "description": "Path of the subconstruction's model file.",
                                },
                                "name": {
                                    "type": "string",
                                    "description": "What to call it in the scene, e.g. 'house'.",
                                },
                                "x": {"type": "number", "description": "Optional placement in LDU."},
                                "y": {"type": "number", "description": "Optional placement in LDU."},
                                "z": {"type": "number", "description": "Optional placement in LDU."},
                            },
                            "required": ["file"],
                        },
                    },
                    "title": {"type": "string", "description": "Name of the finished scene."},
                },
                "required": ["path", "components"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Report that you cannot go on. **You do not use this to "
                "finish** - whether the build is done is not yours to decide "
                "and not yours to announce. Your requirements are checked "
                "against the model every time you call validate_model, and the "
                "run ends by itself the moment they are all met. Keep "
                "building until that happens. Call this ONLY with "
                "give_up=true, when something genuinely stops you: a part that "
                "does not exist, geometry that will not resolve, a requirement "
                "that cannot be built. That is an honest answer and it is "
                "accepted. Calling it to claim success is refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": (
                            "What you built and what the checks said, in a "
                            "few sentences. This is what the user reads."
                        ),
                    },
                    "give_up": {
                        "type": "boolean",
                        "description": (
                            "True when you are stopping without finishing. "
                            "Honest and accepted; claiming success is not."
                        ),
                    },
                    "blocked_by": {
                        "type": "string",
                        "description": (
                            "Required with give_up: what stopped you, "
                            "specifically. 'The 4x4 slope I need is not in the "
                            "catalogue' - not 'it did not work'."
                        ),
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# Grafting, on or off
#
# `copy_from_set` can be withdrawn for a run - see COPY_FROM_SET_ENABLED in
# config.py for why anyone would want that. Withdrawn means *withdrawn*, not
# discouraged: the schema is never shown, so there is no tool for the model to
# decide to call, which is the only form of "off" that a prompt cannot argue
# its way around.
#
# A module-level flag rather than a parameter threaded through six call sites,
# for the same reason render.set_model is one: it is a single choice made once
# per process, by the settings dialog or by the environment, and every agent in
# the process is meant to see the same answer.
_COPY_FROM_SET = COPY_FROM_SET_ENABLED


def set_copy_from_set(enabled):
    """Turn grafting from real sets on or off for this process."""
    global _COPY_FROM_SET
    _COPY_FROM_SET = bool(enabled)


def copy_from_set_enabled():
    return _COPY_FROM_SET


def agent_tools():
    """The schemas an agent may be shown, as things stand.

    Everything that builds a tool list starts here rather than from
    ``TOOL_SCHEMAS`` - the orchestrator's per-phase narrowing, the app's chat
    turn, the bare agent. TOOL_SCHEMAS remains the full catalogue of what
    exists, which is what the retry repairer and the trace need.
    """
    if _COPY_FROM_SET:
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS
            if t["function"]["name"] != "copy_from_set"]


def _resolve(path):
    """Resolve a tool-supplied path inside out/, refusing escapes."""
    p = Path(path)
    if p.is_absolute():
        target = p.resolve()
    else:
        target = (OUT_DIR / p).resolve()
    root = PROJECT_ROOT.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path escapes the project directory: {path}")
    return target


def _retrieval():
    """The vector-search layer, imported lazily.

    It pulls in torch and a 600M-parameter model, which is a second or two the
    first time. Doing it at module import would make every process that merely
    touches ``tools`` pay for it, including ones that never search.
    """
    from ..retrieval import search

    return search


def _part_lines(text):
    """``[(line_no, part, x, z, matrix)]`` for the real parts in an LDraw doc."""
    blocks = {ln.strip()[7:].strip().lower() for ln in text.splitlines()
              if ln.strip().lower().startswith("0 file ")}
    out = []
    for number, line in enumerate(text.splitlines(), start=1):
        fields = line.split()
        if len(fields) < 15 or fields[0] != "1":
            continue
        target = " ".join(fields[14:]).strip().lower()
        if target in blocks:
            continue
        try:
            values = [float(v) for v in fields[2:14]]
        except ValueError:
            continue
        out.append((number, target, values[0], values[2], values[3:12]))
    return out


def _inherent_faults(lines):
    """Which of these lines are already faulty on their own, as 0-based offsets.

    A graft is a rigid copy of a real released set, and real released sets do
    not all pass this checker: 41590 Iron Man has two tiles the connectivity
    check calls misaligned because they are held sideways by a bracket rather
    than seated on a stud. That model was designed, moulded and sold. Refusing
    to graft an assembly because the shipped set has the same complaint would
    make the whole reference corpus unusable for the one thing it is for.

    So the assembly is validated on its own first, and whatever it is already
    guilty of is not counted against it again. Anything the graft *introduces* -
    a collision with what it is joining - still blocks.
    """
    import tempfile

    head = _HEADER.format(name="graft.ldr", title="graft")
    offset = len(head.splitlines()) + 1
    handle = tempfile.NamedTemporaryFile("w", suffix=".ldr", delete=False,
                                         encoding="utf-8")
    try:
        handle.write(head + "\n".join(lines) + "\n")
        handle.close()
        report = validation.validate(handle.name, max_listed=200)
    except Exception:
        return set()
    finally:
        try:
            Path(handle.name).unlink(missing_ok=True)
        except OSError:
            pass

    found = set()
    for row in (report.get("connectivity") or {}).get("misaligned_parts") or []:
        number = row.get("line")
        if isinstance(number, int):
            found.add(number - offset)
    for row in report.get("overcrowded_studs") or []:
        number = row.get("line")
        if isinstance(number, int):
            found.add(number - offset)
    return found


def _new_line_faults(text, first_new, last_new, inherent=(), ignore=()):
    """What is wrong with the parts this call is adding. ``{}`` when nothing is.

    Run over the whole candidate model, because a new brick collides with an
    old one rather than with itself - but only faults involving a line this
    call is responsible for may block it. A model that already had faults is
    not a reason to refuse to add a chimney to it, and refusing would leave the
    builder unable to write anything until it had repaired work it may not have
    done.

    This is the same check `validate_model` runs, moved to write time. It costs
    about a tenth of a second and it catches, on the first call, what the build
    that prompted it did not discover until its fifteenth.
    """
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".ldr", delete=False,
                                         encoding="utf-8")
    try:
        handle.write(text if text.endswith("\n") else text + "\n")
        handle.close()
        report = validation.validate(handle.name, max_listed=8)
    except Exception:
        # The gate is an improvement on writing blind, never a reason a build
        # cannot proceed. A checker that fails lets the write through, and
        # validate_model has the last word as it always did.
        return {}
    finally:
        try:
            Path(handle.name).unlink(missing_ok=True)
        except OSError:
            pass

    excused = {first_new + offset for offset in (inherent or ())}

    def mine(row):
        touched = False
        for key in ("line", "a", "b"):
            value = row.get(key)
            number = value.get("line") if isinstance(value, dict) else value
            if isinstance(number, int) and first_new <= number <= last_new:
                if number in excused:
                    continue
                touched = True
        return touched

    found = {}
    for name, rows in (
            ("overlapping_parts",
             (report.get("collision") or {}).get("overlapping_parts")),
            ("misaligned_parts",
             (report.get("connectivity") or {}).get("misaligned_parts")),
            ("overcrowded_studs", report.get("overcrowded_studs"))):
        if name in ignore:
            continue
        hit = [r for r in (rows or []) if mine(r)]
        if hit:
            found[name] = hit
    return found


_HEADER = ("0 FILE {name}\n0 {title}\n0 Name: {name}\n0 Author: Maister Builder\n"
           "0 !LDRAW_ORG Model\n"
           "0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt\n")


def _phase_conflict(text, first_new, last_new):
    """A refusal when these parts stand on a different stud lattice, or None.

    The failure this exists for looks like nothing at all in the source. A 6x6
    plate at x = -180 and a 1x1 plate at x = 140 are both on multiples of 20 and
    both perfectly reasonable - and their studs are half a stud apart, so they
    can never connect. One build spent fifteen calls laying two such lattices
    over each other and only found out when it validated at the end, by which
    time thirty-six parts were on the wrong one.

    So the model's own lattice is measured from what is already in it, and
    anything joining it has to agree. See lattice.py.
    """
    from . import lattice

    rows = _part_lines(text)
    old = [(p, x, z, m) for n, p, x, z, m in rows if n < first_new]
    new = [(n, p, x, z, m) for n, p, x, z, m in rows
           if first_new <= n <= last_new]
    if not new:
        return None

    # What the model already stands on. With nothing there yet, these ops set
    # it themselves and only have to agree with each other.
    target = lattice.dominant(old) if old else lattice.dominant(
        [(p, x, z, m) for _, p, x, z, m in new])
    if target is None:
        return None

    off, corrections = [], set()
    for number, part, x, z, matrix in new:
        here = lattice.phase(part, x, z, matrix)
        if here is None:
            continue
        delta = (lattice.correction(here[0], target[0]),
                 lattice.correction(here[1], target[1]))
        if delta == (0, 0):
            continue
        corrections.add(delta)
        off.append({"line": number, "part": part,
                    "position": [round(x, 1), round(z, 1)],
                    "move": lattice.describe(part, x, z, matrix, target)})
    if not off:
        return None

    # One correction for all of them is a phase slip, not a design mistake:
    # the whole call is half a stud out of step with the model and the fix is
    # to shift it back. Refusing that costs the builder a step to be told a
    # number this already knows - and the last time it happened three times in
    # one run, the builder gave up on the tool and hand-wrote the parts through
    # `edit_model`, which checks nothing. So it is applied rather than
    # reported, and the reply says what moved.
    if len(corrections) == 1:
        return {"shift": corrections.pop(), "moved": off}

    established = ("the model this is being added to"
                   if old else "the first of these ops")
    return {
        "error": (f"nothing was written: {len(off)} of these parts would stand "
                  f"on a different stud lattice from {established}, half a stud "
                  f"out, where they can never connect to it"),
        "wrong_lattice": off[:8],
        "why": ("A part's studs sit at a fixed offset from its origin, and the "
                "offset depends on the part: a 6x6 plate's studs are at ±10, "
                "±30, ±50 from its centre, a 1x1 plate's stud is at 0. So two "
                "parts can both sit on multiples of 20 and still be half a "
                "stud apart. That is what has happened here."),
        "hint": ("Apply the move listed against each part - ±10 on one axis or "
                 "both - and call build_ops again. Usually it is the same move "
                 "for all of them, so it is one correction to the `at` of each "
                 "op rather than a redesign. If the offset is deliberate "
                 "because these parts sit on jumper plates, pass "
                 "`allow_half_offset: true`."),
    }


def _write_parts(path, lines, mode="append", submodel=None, title=None,
                 allow_half_offset=False, state=None, action="built",
                 source="these ops", tool="build_ops", inherent=(),
                 definitions=(), ignore_faults=()):
    """Put part lines into a model, or refuse and leave the file alone.

    Shared by `build_ops` and `copy_from_set`, because "add these parts and
    check them before anything is written" is the same job whether the lines
    were compiled from an operation or lifted out of a real set. Returns the
    persisted result, or an error dict describing what stopped it.
    """
    target = _resolve(path)
    existing = (target.read_text(encoding="utf-8", errors="replace")
                if target.is_file() else "")
    mode = (mode or "append").lower()
    if mode not in ("append", "replace"):
        return {"error": f"mode must be 'append' or 'replace', not {mode!r}"}

    if mode == "replace" or not existing.strip():
        name = Path(path).name
        head = _HEADER.format(name=name, title=title or Path(path).stem)
        combined = head + "\n" + "\n".join(lines) + "\n"
        first_new = len(head.splitlines()) + 2
    else:
        existing_lines = existing.splitlines()
        headers = [(i, ln.strip()[7:].strip()) for i, ln in enumerate(existing_lines)
                   if ln.strip().lower().startswith("0 file ")]
        # A block whose name ends in .dat is a *part definition*, not a
        # submodel - a printed tile a graft brought with it. It is not
        # somewhere parts can be added, and it must not be counted when
        # deciding whether the file is ambiguous: after one graft a plain model
        # can easily hold six of them, and counting those refused every
        # subsequent write to a file with exactly one model in it.
        models = [(i, n) for i, n in headers if not n.lower().endswith(".dat")]

        start = None
        if submodel:
            wanted = str(submodel).strip().lower()
            start = next((i for i, n in headers if n.lower() == wanted), None)
            if start is None:
                return {"error": f"no submodel named `{submodel}` in {path}",
                        "submodels": [n for _, n in models]}
        elif len(models) > 1:
            return {
                "error": (f"`{path}` holds {len(models)} submodels, so there is "
                          f"no single end to append to"),
                "submodels": [n for _, n in models],
                "hint": ("Name the one these parts belong in with `submodel`, "
                         "or write to that submodel's own file instead."),
            }
        elif models:
            start = models[0][0]

        if start is None:
            # No FILE headers at all: the whole file is one implicit block.
            body = existing.rstrip("\n")
            combined = body + "\n" + "\n".join(lines) + "\n"
            first_new = len(body.splitlines()) + 1
        else:
            # The end of *this block*, which is where the next header begins -
            # not the end of the file. Appending past a part definition would
            # put the model's bricks inside the definition of a printed tile.
            end = next((i for i in range(start + 1, len(existing_lines))
                        if existing_lines[i].strip().lower().startswith("0 file ")),
                       len(existing_lines))
            # Back up over the blank lines that separate one block from the
            # next, so the parts land inside this submodel and not in the gap
            # before the following one.
            while end > start + 1 and not existing_lines[end - 1].strip():
                end -= 1
            combined = "\n".join(existing_lines[:end] + lines + existing_lines[end:]) + "\n"
            first_new = end + 1

    last_new = first_new + len(lines) - 1

    # Part definitions the placements need, as their own blocks at the end of
    # the document - which is where an MPD keeps them. They carry no
    # coordinates in the model's space, so they sit outside the checked range.
    if definitions:
        already = {ln.strip().lower() for ln in combined.splitlines()
                   if ln.strip().lower().startswith("0 file ")}
        fresh, skip = [], False
        for line in definitions:
            head = line.strip().lower()
            if head.startswith("0 file "):
                skip = head in already
                already.add(head)
            if not skip:
                fresh.append(line)
        if fresh:
            combined = combined.rstrip("\n") + "\n" + "\n".join(fresh) + "\n"

    shifted = None
    if not allow_half_offset:
        wrong_phase = _phase_conflict(combined, first_new, last_new)
        if wrong_phase and "shift" in wrong_phase:
            # A uniform half-stud slip: move the new lines onto the model's
            # lattice and carry on, rather than spending the builder a step to
            # be handed the arithmetic.
            dx, dz = wrong_phase["shift"]
            lines = [_shift_line(ln, dx, dz) for ln in lines]
            combined = "\n".join(
                combined.splitlines()[:first_new - 1] + lines
                + combined.splitlines()[last_new:]) + "\n"
            shifted = {"x": dx, "z": dz, "parts": len(wrong_phase["moved"])}
        elif wrong_phase:
            return wrong_phase

    faults = _new_line_faults(combined, first_new, last_new, inherent,
                              ignore_faults)
    if faults:
        counted = ", ".join(
            f"{len(rows)} {name.replace('_', ' ')}" for name, rows in faults.items())
        out = {
            "error": (f"nothing was written: the parts {source} place would "
                      f"leave the model with {counted}"),
            **faults,
            "hint": (f"Each fault names the line it is on; the lines from "
                     f"{first_new} onward are the ones {source} would have "
                     f"added. Move what is wrong and call {tool} again - the "
                     f"model on disk is untouched, so nothing needs undoing."),
        }
        fix = _proposed_fix(combined, first_new, last_new, faults,
                            inherent, ignore_faults)
        if fix:
            out["try_this"] = fix
            out["hint"] = (
                f"`try_this` is a correction that was applied to a copy and "
                f"re-checked, and it came back clean - it is not a guess. "
                f"Apply those moves to the ops that placed the lines named and "
                f"call {tool} again. The model on disk is untouched.")
        return out

    result = _persist(target, path, combined, state=state, action=action)
    result["mode"] = mode
    if submodel:
        result["submodel"] = submodel
    if shifted:
        result["aligned_to_lattice"] = {
            **shifted,
            "note": (f"these parts were half a stud out of phase with the "
                     f"model, so all {shifted['parts']} of them were moved "
                     f"x{shifted['x']:+g}, z{shifted['z']:+g} onto the lattice "
                     f"the rest of it uses. Their studs line up now. Take that "
                     f"offset into account for the next call rather than "
                     f"repeating it."),
        }
    return result


def _proposed_fix(combined, first_new, last_new, faults, inherent, ignore_faults):
    """A correction that was tried and re-checked, or None.

    A rejection used to hand back a fault list and leave the builder to derive
    the correction itself. Measured across the recorded runs that cost 129
    extra rounds - 42% of every successful write was preceded by at least one
    rejection - and the geometry needed to answer it was already computed:
    `collisions._describe` works out the shortest legal move that separates an
    overlapping pair and puts it in `suggested_move`.

    So the moves are collected, applied to a copy, and the copy is re-checked.
    Only a correction that actually comes back clean is offered, because a
    suggestion that does not work is worse than none - it costs the round it
    was meant to save and it teaches the builder to stop reading the field.
    """
    moves = {}
    for row in faults.get("overlapping_parts") or []:
        move = row.get("suggested_move") or {}
        axis, ldu = move.get("axis"), move.get("ldu")
        if axis not in ("x", "y", "z") or not ldu:
            continue
        # `b` is the part the move was worked out for; only move a line this
        # call is adding, never one that was already in the model.
        line = (row.get("b") or {}).get("line")
        if not isinstance(line, int) or not first_new <= line <= last_new:
            line = (row.get("a") or {}).get("line")
            if not isinstance(line, int) or not first_new <= line <= last_new:
                continue
            ldu = -ldu
        moves.setdefault(line, {"x": 0.0, "y": 0.0, "z": 0.0})[axis] += ldu
    if not moves:
        return None

    lines = combined.splitlines()
    for line, delta in moves.items():
        if 1 <= line <= len(lines):
            lines[line - 1] = _shift_line(lines[line - 1], delta["x"],
                                          delta["z"], delta["y"])
    candidate = "\n".join(lines) + "\n"
    if _new_line_faults(candidate, first_new, last_new, inherent, ignore_faults):
        return None    # the move did not settle it; say nothing rather than guess

    return {
        "moves": [{"line": line,
                   **{axis: round(value, 1)
                      for axis, value in delta.items() if value}}
                  for line, delta in sorted(moves.items())],
        "checked": ("applied to a copy of the model and re-validated: this "
                    "leaves no overlapping parts"),
    }


def _shift_line(line, dx, dz, dy=0.0):
    """One LDraw line moved, or unchanged if it is not a part line."""
    tokens = line.split()
    if len(tokens) < 15 or tokens[0] != "1":
        return line
    try:
        x, y, z = float(tokens[2]), float(tokens[3]), float(tokens[4])
    except ValueError:
        return line

    def number(value):
        rounded = round(float(value), 3)
        return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"

    tokens[2:5] = [number(x + dx), number(y + dy), number(z + dz)]
    return " ".join(tokens)


def _record_ops(target, mode, submodel, entry):
    """Keep the build sequence beside the model.

    A build is a program, and keeping only the coordinates it produced throws
    the program away. Grafts go in the same history as ops, in the order they
    happened, so the file reads as how the model was actually made.
    """
    try:
        record = target.with_suffix(target.suffix + ".ops.json")
        history = []
        if record.is_file():
            try:
                history = json.loads(record.read_text(encoding="utf-8")) or []
            except ValueError:
                history = []
        if mode == "replace":
            history = []
        history.append({"mode": mode, "submodel": submodel, **entry})
        record.write_text(json.dumps(history, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    except OSError:
        pass


def _model_phase(target, submodel=None):
    """The stud lattice a model already stands on, as ``(phase_x, phase_z)``.

    None for a file that does not exist yet or holds nothing the stud grid
    governs - in which case the ops about to be written set the lattice
    themselves and only have to agree with each other.
    """
    from . import lattice
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = _part_lines(text)
    if not rows:
        return None
    return lattice.dominant([(p, x, z, m) for _, p, x, z, m in rows])


def _build_ops(path, ops, mode="append", submodel=None, title=None,
               allow_half_offset=False, state=None):
    """Compile a build sequence into parts and put them in the model."""
    from . import buildir

    # The lattice the destination already stands on, so the ops are laid down
    # in its phase rather than discovering the clash after they are compiled.
    # Skipped when the caller says the offset is deliberate - that is what a
    # jumper plate is for. See buildir._snap_to_phase.
    phase = None
    if not allow_half_offset and mode != "replace":
        phase = _model_phase(_resolve(path), submodel)

    try:
        lines, report = buildir.compile_ops(ops, phase=phase)
    except buildir.BuildError as exc:
        return {"error": str(exc),
                "hint": ("Fix that op and call build_ops again. The ops that "
                         "place parts are place, row, grid, stack, ring, "
                         "mirror, wall, box and fill; repeat, reflect, define "
                         "and call take an `ops` list and say what happens to "
                         "it. Spacing is worked out from the part, so never "
                         "pass pitch_ldu unless a deliberate gap is wanted.")}

    result = _write_parts(path, lines, mode=mode, submodel=submodel,
                          title=title, allow_half_offset=allow_half_offset,
                          state=state, action="built", source="these ops",
                          tool="build_ops")
    if "error" in result:
        return result

    result.update({"ops": report["ops"], "parts_placed": report["parts_placed"],
                   "steps": report["steps"]})
    if report.get("expanded_to"):
        # A grouped call: say what the groups came to. Without it the reply
        # reads as though every one of those ops was written out, which is the
        # thing the groups exist to stop the builder doing next time.
        result["ops_written"] = report["ops_written"]
        result["expanded_to"] = report["expanded_to"]
        result["groups"] = (
            f"{report['ops_written']} op(s) as written expanded to "
            f"{report['expanded_to']}. The repeats and reflections did the "
            f"copying; do not write those copies out by hand to check them.")
    _record_ops(_resolve(path), result.get("mode"), submodel,
                {"ops": ops if isinstance(ops, list) else [ops]})
    result["next"] = ("Now validate_model on this path - it checks the grid and "
                      "renders the model. build_ops only checked the parts it "
                      "placed itself.")
    return result


def _copy_from_set(path, set_number, submodel=None, at=None, rotate=0,
                   recolour=None, anchor="bottom-centre", mode="append",
                   into_submodel=None, title=None, allow_half_offset=False,
                   only_parts=None, exclude_parts=None, matching=None,
                   state=None):
    """Graft a real set's assembly into the model being built."""
    from . import graft

    # The schema is withheld when grafting is off, so a call arriving here is
    # either a model inventing a tool it was never offered or a caller that
    # built its list from TOOL_SCHEMAS by hand. Refuse rather than write: a
    # setting whose enforcement lives only in the schema is a setting that is
    # off until something goes wrong.
    if not _COPY_FROM_SET:
        return {"error": "copy_from_set is switched off for this run - this "
                         "model is to be designed rather than assembled out "
                         "of released sets. Read the sets if they help "
                         "(`read_model(\"set:<n>\")`), then build it with "
                         "`build_ops` and `edit_model`."}

    # Which lattice the destination is on, so the assembly joins it rather than
    # bringing its own. An empty file has none and the graft sets it.
    target = _resolve(path)
    existing = (target.read_text(encoding="utf-8", errors="replace")
                if target.is_file() and (mode or "append").lower() != "replace"
                else "")
    target_phase = None
    if existing.strip():
        from . import lattice as lattice_module
        target_phase = lattice_module.dominant(
            [(p, x, z, m) for _, p, x, z, m in _part_lines(existing)])

    try:
        parts, meta = graft.extract(set_number, submodel,
                                    only_parts=only_parts,
                                    exclude_parts=exclude_parts,
                                    matching=matching)
        lines, placed = graft.place(parts, at if at is not None else [0, 0, 0],
                                    rotate=rotate, recolour=recolour,
                                    anchor=anchor, target_phase=target_phase)
    except graft.GraftError as exc:
        return {"error": str(exc),
                "hint": ("get_set_details lists a set's assemblies with their "
                         "part counts; read the one you want with "
                         "read_model(\"set:<number>\", submodel=\"<name>\") "
                         "before grafting it.")}

    # The provenance comment goes in the file, not just the reply: a model that
    # borrowed a wheel arch should say so to whoever opens it next.
    lines = [graft.credit({**meta, "parts": placed["parts"]})] + lines

    # Printed elements are defined inside the set's own file, so they come with
    # it or the graft references parts that exist nowhere.
    definitions = []
    for _key, block in meta.get("embedded") or ():
        definitions.append("")
        definitions.extend(block)

    # The per-part phase check is skipped, deliberately: it is aligned above as
    # a *body*, and inside a real set there are genuine half-stud offsets built
    # on jumper plates. Judging those part by part would refuse every assembly
    # LEGO ever designed. The grid check below still runs, and it asks the
    # question that actually matters - is each part seated on something.
    # A *filtered* graft is a handful of parts lifted out of an assembly, so
    # whatever used to hold them up was left behind on purpose. They arrive
    # unsupported and the builder attaches them next - which is not a fault in
    # the graft, it is the middle of the job. Overlaps still block, because two
    # parts in one space is never the middle of anything.
    partial = bool(only_parts or exclude_parts or matching)
    result = _write_parts(path, lines, mode=mode, submodel=into_submodel,
                          title=title, allow_half_offset=True,
                          state=state, action="grafted",
                          source="this assembly", tool="copy_from_set",
                          inherent=_inherent_faults(lines),
                          definitions=definitions,
                          ignore_faults=("misaligned_parts",) if partial else ())
    if "error" in result:
        return result

    result.update({"from": meta, "placed": placed})
    if partial:
        result["attach_these"] = (
            "These parts were taken out of an assembly without whatever was "
            "holding them, so they are sitting where you put them rather than "
            "on anything. Attach them - move them onto real studs with "
            "edit_model, or build up to them with build_ops - before you "
            "finish: validate_model will report them as off the grid until you "
            "do, and no run may end holding one.")
    _record_ops(_resolve(path), result.get("mode"), into_submodel,
                {"graft": {"set": meta.get("set_number"),
                           "submodel": meta.get("submodel"), "at": at,
                           "rotate": rotate, "recolour": recolour,
                           "anchor": anchor}})
    result["next"] = (
        "It is in your model now, as ordinary part lines you own - change them "
        "with edit_model, recolour them, take pieces out, build onto them. Then "
        "validate_model.")
    return result


def _plan_construction(subject, requirements=None, model_path=None,
                       max_pieces=None, footprint_studs=None,
                       use_references=True, should_stop=None, state=None):
    current = None
    if model_path:
        target = _resolve(model_path)
        if not target.is_file():
            return {"error": f"no such file: {model_path}",
                    "hint": "Omit model_path to plan a new model from nothing."}
        current = target.read_text(encoding="utf-8", errors="replace")

    try:
        return blueprint.plan(
            subject,
            requirements=requirements,
            current_model=current,
            max_pieces=max_pieces,
            footprint_studs=footprint_studs,
            use_references=use_references is not False,
            should_stop=should_stop,
            # The look was settled before this run started. The builder does
            # not carry it into the call - it is on the ledger, so a plan is
            # written against the brief whether or not the builder thought to
            # mention it.
            design_brief=getattr(state, "brief", None),
            # And so were the reference sets. On the ledger for the same
            # reason, and for one more: the plan and the builder have to be
            # looking at the *same* sets, or the plan names an assembly the
            # builder was never handed and cannot graft.
            reference_sets=getattr(state, "reference_sets", None),
            # And what this builder already worked out on a subject like this.
            # On the ledger for the same reason again - see recall.py.
            recalled=getattr(state, "recalled", None),
            # What the harness read off the workbench before the run started.
            # The raw source below says what lines are in the file; this says
            # what they *are*, which is the question a plan for a change has to
            # answer first.
            workbench=getattr(state, "workbench", None),
        )
    except blueprint.PlanningFailed as exc:
        return {"error": str(exc),
                "hint": "Plan the build yourself and carry on: split it into "
                        "subtasks, compute the Y levels, then search_parts."}


def _search_parts(query="", category=None, width_studs=None, depth_studs=None,
                  max_results=12, state=None):
    max_results = int(max_results or 12)
    failure = None
    # A wider slice than is shown: the tail is what the shape map is drawn
    # from, and the search has already ranked it either way.
    wanted = max_results * 4
    try:
        found = _retrieval().search_parts(query, category, width_studs,
                                          depth_studs, wanted)
        results, tail = found[:max_results], found[max_results:]
    except Exception as exc:
        # never let a retrieval problem stop the agent finding a brick
        failure = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        results, tail = catalog.search_parts(query, category, width_studs,
                                             depth_studs, max_results), []
        if results:
            return {"results": results,
                    "note": f"keyword search only; semantic search unavailable ({failure})"}

    if not results:
        # "No match" and "the search broke" are the same empty list and must
        # never read the same way. Told the first when the second is true, the
        # agent concludes the part does not exist - and the next thing it does
        # is invent a part number for it.
        if failure:
            return {"results": [], "error": f"the part search failed: {failure}",
                    "hint": "This is NOT evidence that no such part exists. Do "
                            "not guess a part number. Try once more with "
                            "catalogue wording ('slope curved 2 x 2'), which "
                            "does not need the semantic index."}
        return {"results": [],
                "hint": "No match. Try fewer words, describe the shape instead, "
                        "or drop the category filter."}

    info = _retrieval().status().get("parts") or {}
    if not info.get("available"):
        return {"results": results,
                "note": "keyword search only - the parts vector database is not "
                        "built, so describing a shape in plain words will not work. "
                        "Build it with: python -m maister.retrieval.build_indexes"}
    return {"results": results,
            **_companions_for(results),
            **_shape_map(category, results, tail),
            **_remember_parts(state, query, results)}


# How many companion parts ride along with a search. Five is enough to catch
# the other half of anything that comes in halves, and few enough that it stays
# a footnote rather than a second page of results.
COMPANIONS_RETURNED = 5


def _companions_for(results):
    """The parts that go with what was found - deduplicated, best first.

    Half the catalogue is half of something. A search for a wheel rim answers
    with rims, and every one of them is a hubcap until its tyre goes on; the
    tyre is a different part number and no wording of the query returns both.
    So the companions of the results ride along with them.

    Held in a set on the way out, and checked against the results themselves.
    Twelve results with five companions each is sixty entries of which most are
    the same handful of parts, and putting the same paragraph into the context
    six times costs the room that a sixth result would have used.
    """
    from . import companions

    already = {str(r.get("part_id") or "").lower() for r in results}
    best = {}
    for row in results:
        for mate in companions.for_part(row.get("part_id"), limit=4):
            key = str(mate.get("part_id") or "").lower()
            if not key or key in already:
                continue
            # the strongest claim wins: a part that is the other half of one
            # result at 93% beats being a loose associate of three at 20%
            if mate["in_sets_pct"] > best.get(key, {}).get("in_sets_pct", -1):
                best[key] = {"part_id": mate["part_id"],
                             "description": mate.get("description"),
                             "in_sets_pct": mate["in_sets_pct"],
                             "goes_with": row.get("part_id")}
    if not best:
        return {}
    ranked = sorted(best.values(), key=lambda m: -m["in_sets_pct"])
    return {
        "companion_parts": ranked[:COMPANIONS_RETURNED],
        "companion_parts_note": (
            "Parts that real sets put alongside the results above, with how "
            "often. A high percentage means the result is half of an assembly "
            "- place the companion too, or the assembly is unfinished."),
    }


def _remember_parts(state, query, results):
    """Write what was found into the project's palette, and hand it back.

    The palette is on disk rather than in the conversation because the
    conversation does not survive: each subconstruction is a fresh agent, and
    the parts the last one found scrolled out of a context it never had. A
    builder that cannot see what has already been found searches again, gives
    up, and approximates the shape out of bricks.
    """
    project = getattr(state, "project", None) if state is not None else None
    if not project:
        return {}
    try:
        palette.record(project, results, query=query)
        found = palette.summary(project)
    except Exception:
        return {}
    return {"parts_you_have_found": found} if found else {}


# How many neighbouring shape families a search reports back. Enough to show
# that a curve could also have been a cylinder, a cone or a dish; few enough
# that it stays a signpost rather than a second set of results.
NEIGHBOURING_CATEGORIES = 6


def _shape_map(category, shown, rest):
    """The shape families this query also reached but did not show.

    A search answers with twelve parts. What it never showed was the shape of
    the space they came out of - that the same words also reached cylinders,
    cones and dishes, any of which might be the better answer. Without that,
    one search returns one idea, and a model told to search sparingly builds
    the whole thing out of the first idea.

    Taken from the candidates the search already ranked and then discarded, so
    it costs nothing and is as relevant as the results themselves. A second
    lexical search would have been neither: "rounded shapes for a tower" has no
    word in it that any catalogue description contains.
    """
    if category or not rest:
        return {}
    seen = {r.get("category") for r in shown}
    tally = {}
    for row in rest:
        name = row.get("category")
        if not name or name in seen:
            continue
        tally[name] = tally.get(name, 0) + 1
    if not tally:
        return {}
    ranked = sorted(tally.items(), key=lambda kv: -kv[1])[:NEIGHBOURING_CATEGORIES]
    return {
        "other_shape_families": [{"category": n, "also_matched": c}
                                 for n, c in ranked],
        "other_shape_families_note": (
            "The same search also reached these families and ran out of room "
            "before showing them. If nothing above is the shape you pictured, "
            "search again with category= set to one of these rather than "
            "settling for a plain brick."),
    }


def _search_sets(query, theme=None, year_min=None, year_max=None,
                 min_pieces=None, max_pieces=None, max_results=8):
    results = _retrieval().search_sets(
        query, theme=theme, year_min=year_min, year_max=year_max,
        min_pieces=min_pieces, max_pieces=max_pieces,
        max_results=int(max_results or 8))
    if not results:
        return {"results": [], **_sets_unavailable_hint()}
    return {"results": results,
            "next": "Call get_set_details or read_set_model on a set_number to "
                    "study how it was built."}


def _find_similar_sets(set_number, theme=None, min_pieces=None, max_pieces=None,
                       max_results=8):
    if not sets.resolve(set_number):
        return {"error": f"no official model for set '{set_number}'",
                "hint": "Use search_sets to find a valid set number."}
    results = _retrieval().find_similar_sets(
        set_number, max_results=int(max_results or 8), theme=theme,
        min_pieces=min_pieces, max_pieces=max_pieces)
    if not results:
        return {"results": [], **_sets_unavailable_hint()}
    return {"results": results}


def _sets_unavailable_hint():
    """Distinguish "nothing matched" from "the index was never built"."""
    info = _retrieval().status().get("sets") or {}
    if info.get("available"):
        return {"hint": "No match. Try a broader description or drop the filters."}
    return {"hint": "The sets vector database is not built yet. Run: "
                    "python -m maister.retrieval.build_indexes --only sets",
            "reason": info.get("reason")}


def _get_set_details(set_number):
    """Open a real set: its LDraw source, and the way into the rest of it.

    This used to lead with metadata - theme, year, piece counts, the twenty
    parts the set used most - and put the source underneath. That is backwards.
    None of it is how the set was *built*: a year and a piece count cannot be
    copied onto a stud grid, and a list of the parts used most is a shopping
    list for a model whose geometry the agent still has not seen. The source is
    the only part of a set that answers "how did they do it", so the source is
    what comes back.

    With it, the index of submodels - because that is what a real set is. A
    2,000-line MPD is thirty named assemblies, and "the one that makes the
    wing" is a thing you can ask for by name and cannot find by guessing line
    numbers.
    """
    rows = sets.resolve(set_number)
    if not rows:
        return {"error": f"no official model for set '{set_number}'",
                "hint": 'Use search_reference(kind="sets") to find one.'}

    head = rows[0]
    source = _read_set_model(set_number, 1, SET_DETAIL_LINES)
    if "error" in source:
        return source

    number = head.get("set_number")
    details = {
        "set_number": number,
        "set_name": head.get("set_name"),
        "model_file": head.get("file_name"),
        "total_lines": source["total_lines"],
        "shown_lines": source["shown_lines"],
        "model_source": source["content"],
    }

    blocks = _set_submodels(head)
    if blocks:
        shown = blocks
        if len(blocks) > MAX_SUBMODELS:
            # A 4,700-line set is 136 blocks, most of them a hinge or a pair of
            # tiles. Listing all of them buries the assemblies worth reading in
            # the ones that are not, so the biggest survive - then put back in
            # build order, because that order is itself information.
            shown = sorted(sorted(blocks, key=lambda b: -b["parts"])[:MAX_SUBMODELS],
                           key=lambda b: b["starts_at"])
            details["submodels_note"] = (
                f"the {MAX_SUBMODELS} largest of {len(blocks)} submodels")
        details["submodels"] = shown
        biggest = max(shown, key=lambda b: b["parts"])
        details["next"] = (
            f"{len(blocks)} submodels. Read the one that makes the part you "
            f"are stuck on - the largest is "
            f"read_model('set:{number}', submodel='{biggest['name']}')")
    elif source.get("more"):
        details["next"] = (
            f"{source['more'].split(';')[0]} - "
            f"read_model('set:{number}', "
            f"start_line={source['shown_lines'][1] + 1}) for the rest")

    if len(rows) > 1:
        details["other_models"] = [r["file_name"] for r in rows[1:]]
    return details


def _set_submodels(row):
    """The named blocks of a set's MPD: what it is assembled out of.

    Each with the line it starts on and how many parts are in it, so the agent
    can pick by size as well as by name - the twelve-part block is a detail,
    the hundred-part block is the body.
    """
    path = sets.model_path(row)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks = []
    for number, line in enumerate(lines, start=1):
        match = _FILE_LINE.match(line)
        if match:
            blocks.append({"name": match.group(1), "starts_at": number, "parts": 0})
        elif blocks and _is_part(line):
            blocks[-1]["parts"] += 1
    for index, block in enumerate(blocks):
        end = blocks[index + 1]["starts_at"] - 1 if index + 1 < len(blocks) else len(lines)
        block["lines"] = end - block["starts_at"] + 1
    return blocks


def _read_set_model(set_number, start_line=1, end_line=None, submodel=None):
    rows = sets.resolve(set_number)
    if not rows:
        return {"error": f"no official model for set '{set_number}'",
                "hint": 'Use search_reference(kind="sets") to find one.'}

    row = rows[0]
    path = sets.model_path(row)
    if not path.is_file():
        return {"error": f"model file missing on disk: {row['file_name']}"}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not submodel and not end_line:
        end_line = max(1, int(start_line or 1)) + MAX_REFERENCE_LINES - 1
    window, error = _read_lines(lines, start_line, end_line, submodel,
                                cap=MAX_REFERENCE_LINES)
    if error:
        return {**error, "set_number": row.get("set_number")}

    result = {
        "set_number": row.get("set_number"),
        "set_name": row.get("set_name"),
        "model_file": row.get("file_name"),
        **window,
    }
    if len(rows) > 1:
        result["other_models"] = [r["file_name"] for r in rows[1:]]
    return result


def _get_part_details(part_id, state=None):
    info = catalog.get_part(part_id)
    if info is None:
        return {"error": f"part '{part_id}' is not in the catalogue",
                "hint": "Use search_parts to find a valid part number."}
    info["stud_grid_note"] = (
        "stud_grid entries are (x, z) offsets from this part's origin. A part "
        "placed on top must have its own seat positions land on these."
    )
    # Measured off the part file rather than derived from its bounding box,
    # which is what `stud_grid` is and is only right for rectangles: a 2x2
    # slope has two studs and a box says four. This one also carries the studs
    # on a part's *sides*, which no box can describe at all, with the rotation
    # that puts a part on one.
    studs = catalog.stud_map(info.get("dat_name") or info.get("part_id"))
    if studs:
        info["studs"] = studs
    # For a part that has a direction, the turns handed over ready to use. The
    # search row says it faces a way; this is where the numbers come from, so
    # deciding to turn it costs nothing further. Y only - a Y turn keeps the
    # part flat on the studs and its footprint on the lattice, so it needs no
    # re-checking of anything underneath it.
    if catalog.faces_a_direction(info):
        info["turns"] = {
            "why": "which way this faces is a decision, not a default. Four "
                   "slopes around a roof all placed as drawn give one edge and "
                   "three cliffs; turned to face outward the same four parts "
                   "are a roof.",
            "matrix_for": {"0 (as drawn)": "1 0 0 0 1 0 0 0 1",
                           **{f"{d}°": m for d, m in catalog.Y_TURNS.items()}},
            "with_build_ops": 'pass "rotate": 90, 180 or 270 and the footprint '
                              'turns with the part - a turned 1x4 takes four '
                              'studs in z rather than four in x, and the '
                              'spacing is worked out for you.',
            # Measured, not asserted: an LDraw slope's origin sits on its back
            # stud row rather than in the middle of its footprint, so a quarter
            # turn moves where its cells fall. 3039 turned 90° needs x+10 where
            # unturned it needed z+10. This is the thing that goes wrong when a
            # rotation is written by hand.
            "watch_the_half_stud":
                "many parts with a direction - most slopes among them - have "
                "their origin on an edge rather than at their centre, so "
                "turning one moves its footprint half a stud and it no longer "
                "lands on the same studs. build_ops corrects that for you and "
                "reports the offset it used. Writing the matrix into the file "
                "yourself, the correction is yours to make: place it, then "
                "validate_model.",
        }
    attached = _notes_on("part", info["part_id"])
    if attached:
        info["your_notes"] = attached
    # A part looked up by number is as found as one that came out of a search.
    project = getattr(state, "project", None) if state is not None else None
    if project:
        try:
            palette.record(project, [info], query=f"looked up {part_id}")
        except Exception:
            pass
    return info


def _save_creation(path, name, description, tags=None, require_valid=False):
    """Put a model in the user's gallery.

    Not a tool any more: the gallery is the user's shelf, and deciding what
    goes on it was never something to ask a language model to judge - it was
    withheld unless the turn's wording looked like a request, which is a
    guess about intent standing in for a button. There is a button now, and
    this is what it calls.
    """
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}",
                "hint": "edit_model it first, then save it."}
    if not (name or "").strip():
        return {"error": "a creation needs a name"}

    # The grid check, and only that: whether a model is worth keeping is not a
    # question about how it looks, and putting a vision call between the user
    # and a button they pressed would make saving take seconds and cost a
    # request.
    report = validation.validate(target)
    if require_valid and not report.get("passed"):
        return {"error": f"this model does not validate: {report.get('verdict')}",
                "verdict": report.get("verdict"),
                "connectivity": report.get("connectivity"),
                "hint": "fix what validate_model reports, then save it."}
    record = creations.save(target, name, description, tags, validation=report)

    result = {"saved": creations.summarize(record)}
    try:
        _retrieval().index_creation(record)
        result["indexed"] = True
    except Exception as exc:
        result["indexed"] = False
        result["note"] = f"saved, but not searchable: indexing failed ({exc})"

    if not record.get("validated"):
        result["warning"] = (
            f"saved, but this model does not validate: {report.get('verdict')}. "
            f"It is recorded as failing so a later search can tell.")
    return result


def _search_creations(query, tag=None, validated_only=False, min_pieces=None,
                      max_pieces=None, max_results=8):
    results = _retrieval().search_creations(
        query, tag=tag, validated_only=bool(validated_only),
        min_pieces=min_pieces, max_pieces=max_pieces,
        max_results=int(max_results or 8))
    if not results:
        total = len(creations.load_creations())
        if not total:
            return {"results": [],
                    "hint": "You have not saved any creations yet. Use "
                            "the Save button once a model validates cleanly."}
        return {"results": [],
                "hint": f"No match among your {total} saved creation(s). "
                        f"For how real LEGO sets solve this, use search_sets."}
    return {"results": results}


def _read_creation(name, start_line=1, end_line=None, submodel=None):
    record = creations.resolve(name)
    if record is None:
        return {"error": f"no saved creation '{name}'",
                "hint": 'Use search_reference(kind="creations") to see what '
                        'has been saved.'}

    path = creations.model_path(record)
    if not path.is_file():
        return {"error": f"the model file for '{name}' is missing on disk"}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not submodel and not end_line:
        end_line = max(1, int(start_line or 1)) + MAX_REFERENCE_LINES - 1
    window, error = _read_lines(lines, start_line, end_line, submodel,
                                cap=MAX_REFERENCE_LINES)
    if error:
        return error

    result = dict(creations.summarize(record))
    result.update(window)
    attached = _notes_on("creation", record["name"])
    if attached:
        result["notes"] = attached
    return result


def _notes_on(subject_type, subject_id):
    """Notes attached to a subject, swallowing any retrieval problem.

    Folded into get_part_details, get_set_details and read_creation: knowledge
    the agent wrote down is only worth having if it comes back automatically
    when the subject comes up again.
    """
    try:
        return _retrieval().notes_for(subject_type, subject_id)
    except Exception:
        return []


def _write_file(path, content, state=None):
    """Write a whole model file. Internal - the agent has no tool for this.

    There used to be a `write_model` tool alongside `edit_model`, and the two
    were one job wearing two names: both put LDraw text on disk, both went
    through `_persist`, both rendered. Two tools for one job is a decision the
    model has to make on every write, and the wrong branch of it is expensive -
    retyping ninety-seven lines to change three is how the ninety-seven get
    lost. `edit_model` now treats a missing file as an empty one, which is the
    whole of what the other tool was for.

    What is left is this: the harness composing a file it generated itself -
    an arrangement, an assembled scene. That is not the model deciding to
    rewrite anything, so it does not need a tool.
    """
    target = _resolve(path)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return {"error": "content too large"}
    result = _persist(target, path, content, state=state)
    result["next"] = ("Now call validate_model on this path. It checks the "
                      "grid and renders the model so you can see what you "
                      "actually built - both in the one call.")
    return result


def _persist(target, path, content, state=None, action="written"):
    """Put text on disk, record it in the ledger, and take a picture.

    Shared by edit_model and the harness's own writes, so that a change made
    line by line is the same event as a file composed whole: same record in the
    run's ledger, same automatic render, same question budget restored.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    target.write_text(content, encoding="utf-8")
    lines = content.splitlines()
    n_refs = sum(1 for ln in lines if ln.strip().startswith("1 "))

    result = {action: str(target.relative_to(PROJECT_ROOT)),
              "lines": len(lines), "part_references": n_refs}
    if state is not None:
        state.record_write(path, lines=len(lines), parts=n_refs)

    # A picture, every time, wrong models included. It costs a third of a
    # second and it is what the user is actually waiting for - they would
    # rather see a broken build than read that one exists. The critique is not
    # taken here: that is an API call, and it belongs where the agent asked for
    # it rather than after every write.
    result.update(_quick_render(target, path, state))
    return result


def _edit_model(path, edits, state=None):
    """Put lines into a model file. All the edits, or none of them.

    Line numbers are the file's own, as it stands before this call - the
    numbering the model was just shown by read_model or by a validation report.
    Asking it to predict how its own earlier edits shift the later ones is
    asking it to do bookkeeping instead of building, and it gets it wrong; the
    edits are sorted and applied from the bottom up here instead.

    **A file that is not there is an empty file.** That one line is what lets
    this be the only writing tool: starting a model is inserting every line of
    it before line 1 of nothing, which `_plan_edits` already allows and already
    bounds-checks. `replace` and `delete` against a file with no lines in it
    fail the same way they fail past the end of a real one, which is right -
    there is nothing there to have expectations about.
    """
    target = _resolve(path)
    creating = not target.is_file()
    if not isinstance(edits, list) or not edits:
        return {"error": "give at least one edit"}

    original = ([] if creating else
                target.read_text(encoding="utf-8", errors="replace").splitlines())
    planned, error = _plan_edits(original, edits)
    if error:
        return error

    # The assembly pass may join objects together; it may not take one apart.
    # Checked before anything is written, like every other refusal here.
    if state is not None and getattr(state, "edit_scope", None) == "assembly":
        refused = _assembly_guard(original, planned, state)
        if refused:
            return refused

    lines = list(original)
    # Bottom up, so an edit's line numbers are still the ones it was written
    # against when it is applied.
    for edit in sorted(planned, key=lambda e: e["start"], reverse=True):
        lines[edit["start"] - 1:edit["end"]] = edit["new"]

    content = "\n".join(lines)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return {"error": "the edited file would be too large"}

    result = _persist(target, path, content, state=state,
                      action="written" if creating else "edited")
    result["applied"] = [e["report"] for e in planned]
    result["line_delta"] = len(lines) - len(original)
    result["was_lines"] = len(original)
    result["changed_regions"] = _regions(planned, lines)

    # Spend the assembly budget, and say what is left of it - a limit nobody is
    # told the balance of is a limit they only find out about by hitting it.
    if state is not None and getattr(state, "edit_scope", None) == "assembly":
        changed = sum(
            sum(1 for ln in original[e["start"] - 1:e["end"]] if _is_part(ln))
            + sum(1 for ln in e["new"] if _is_part(ln))
            for e in planned)
        state.parts_edited += changed
        result["assembly_budget"] = {
            "parts_changed_now": changed,
            "parts_changed_so_far": state.parts_edited,
            "parts_left": max(0, ASSEMBLY_EDIT_BUDGET - state.parts_edited),
            "note": "This pass composes finished objects. The budget is for "
                    "joining them - clashing parts, a plate that ties two "
                    "together - not for rebuilding one.",
        }
    # What this edit did to the model, checked now rather than whenever the
    # builder next validates.
    #
    # `build_ops` refuses to write a part that would land off the grid, and
    # `edit_model` cannot: it is also the tool for a hinge held at an angle and
    # a minifigure's arm, neither of which sits on a stud, so a refusal here
    # would block the work only this tool can do. But leaving it silent turned
    # out worse. In one run `build_ops` refused three times, the builder
    # switched to `edit_model`, wrote the same parts off the grid unchecked,
    # and found out at the step limit - the checked tool taught it to use the
    # unchecked one. So this reports, immediately and by line.
    introduced = _edited_line_faults(target, planned, lines)
    if introduced:
        result["this_edit_broke"] = introduced
        result["warning"] = (
            "the lines this edit touched are not sound: "
            + ", ".join(f"{len(rows)} {name.replace('_', ' ')}"
                        for name, rows in introduced.items())
            + ". Each one names its line and what to do. Fix them in your next "
              "edit_model call rather than building on top of them - a model "
              "carrying either cannot be built out of real bricks, and no run "
              "is allowed to end holding a part off the grid.")

    result["next"] = ("Now call validate_model on this path. It checks the "
                      "grid and renders the model so you can see what the "
                      "change actually did - both in the one call.")
    if not [ln for ln in lines if ln.strip()]:
        result["warning"] = ("that emptied the file - there is no model left "
                             "in it")
    return result


def _edited_line_faults(target, planned, lines):
    """Faults on the lines this edit actually touched. ``{}`` when clean.

    Scoped to the edited lines for the same reason `build_ops` scopes its own
    check: a model that already had faults in it is not a reason to refuse to
    change something else, and a report about work the builder did not just do
    is a report it will read as noise.
    """
    touched = set()
    for entry in planned:
        start = entry.get("start") or 1
        touched.update(range(start, start + max(1, len(entry.get("new") or []))))
    if not touched:
        return {}
    try:
        report = validation.validate(target, max_listed=8)
    except Exception:
        return {}

    def mine(row):
        for key in ("line", "a", "b"):
            value = row.get(key)
            number = value.get("line") if isinstance(value, dict) else value
            if isinstance(number, int) and number in touched:
                return True
        return False

    found = {}
    for name, rows in (
            ("misaligned_parts",
             (report.get("connectivity") or {}).get("misaligned_parts")),
            ("overlapping_parts",
             (report.get("collision") or {}).get("overlapping_parts")),
            ("overcrowded_studs", report.get("overcrowded_studs"))):
        hit = [r for r in (rows or []) if mine(r)]
        if hit:
            found[name] = hit
    return found


def _plan_edits(original, edits):
    """Check every edit against the file. Returns ``(planned, error_or_None)``.

    Nothing is written while anything is wrong. A half-applied batch is the one
    outcome worse than a refused one: the model is then neither what it was nor
    what was asked for, and the line numbers in hand no longer describe either.
    """
    total = len(original)
    planned = []

    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            return None, {"error": f"edit {index} is not an object"}
        op = str(edit.get("op") or "").strip().lower()
        if op not in ("replace", "insert", "delete"):
            return None, {"error": f"edit {index}: unknown op '{op}'",
                          "hint": "op must be 'replace', 'insert' or 'delete'"}

        try:
            start = int(edit.get("start_line"))
        except (TypeError, ValueError):
            return None, {"error": f"edit {index} ({op}) needs a start_line"}

        if op == "insert":
            # Inserting before total + 1 is appending, and is the only line
            # number past the end that means anything.
            if not 1 <= start <= total + 1:
                return None, {
                    "error": f"edit {index} (insert): start_line {start} is "
                             f"outside the file, which has {total} lines",
                    "hint": f"insert before a line from 1 to {total}, or use "
                            f"start_line={total + 1} to add at the end"}
            end = start - 1
        else:
            end = edit.get("end_line")
            end = start if end in (None, "") else int(end)
            if not 1 <= start <= total or not start <= end <= total:
                return None, {
                    "error": f"edit {index} ({op}): lines {start}-{end} are "
                             f"outside the file, which has {total} lines",
                    "hint": "read_model to see the line numbers as they are now"}

        new = _as_lines(edit.get("lines"))
        if op == "delete":
            new = []
        elif not new:
            return None, {
                "error": f"edit {index} ({op}) has no `lines` to put in",
                "hint": "use op 'delete' to take lines out without replacing them"}

        # The guard. A line number the model worked out from an older view of
        # the file is the failure this tool is most exposed to, and without a
        # check it fails silently - the wrong brick is deleted and everything
        # downstream reports on a model nobody meant to build.
        if op in ("replace", "delete"):
            expect = edit.get("expect")
            if expect is None or not str(expect).strip():
                return None, {
                    "error": f"edit {index} ({op}) needs `expect`: the text "
                             f"that is currently on line {start}",
                    "hint": "It is checked before anything is written, so a "
                            "line number that has drifted is caught instead of "
                            "silently changing the wrong part."}
            actual = original[start - 1]
            if str(expect).strip() != actual.strip():
                return None, {
                    "error": f"edit {index} ({op}): line {start} is not what "
                             f"you expected, so nothing was changed",
                    "expected": str(expect).strip(),
                    "actual": actual.strip(),
                    "context": _numbered(original, start - 2, start + 2),
                    "hint": "Your line numbers are stale. Take them from the "
                            "listing above, or read_model, and edit again."}

        where = (f"before line {start}" if op == "insert"
                 else f"line {start}" if end == start
                 else f"lines {start}-{end}")
        planned.append({"start": start, "end": end, "new": new, "op": op,
                        "report": {"op": op, "at": where,
                                   "removed": end - start + 1,
                                   "added": len(new)}})

    # Two edits over the same line have no defined result - whichever ran last
    # would win, and which that is depends on an ordering the caller did not
    # choose.
    for a, b in _pairs(planned):
        if a["start"] <= b["end"] and b["start"] <= a["end"]:
            return None, {
                "error": f"two edits cover the same lines "
                         f"({a['start']}-{a['end']} and {b['start']}-{b['end']})",
                "hint": "Combine them into one edit over the whole range."}
        if a["op"] == "insert" and b["op"] == "insert" and a["start"] == b["start"]:
            return None, {
                "error": f"two inserts at line {a['start']}, in no defined order",
                "hint": "Combine them into one insert with all the lines in it."}

    return planned, None


# --------------------------------------------------------------------------
# What the assembly pass may change
#
# The pass that composes finished subconstructions into a scene now gets
# `edit_model`, because there is small work at the joins that moving whole
# objects cannot do: take out the two bricks where a man's arm passes through
# a tree, put a plate under both so they stand on one base, drop a duplicate
# placement line. None of that is expressible as "shift this object 20 LDU".
#
# What it must not do is rebuild a component. Those arrived correct and
# validated, from a builder that spent twenty steps on them, and a pass with
# ten steps and a spacing problem to fix has no business redesigning one. The
# tool used to be withheld entirely for exactly that reason.
#
# So the difference is enforced rather than asked for. Prose in a prompt is
# what was tried before and it is what failed. A budget in part lines is not
# arguable: a handful of parts is joining, a quarter of a tree is a redesign,
# and the guard can tell them apart without knowing anything about trees.
# --------------------------------------------------------------------------

# Part lines one call may add or remove.
ASSEMBLY_EDIT_PARTS = 8
# …and across the whole assembly pass, so it cannot do it eight at a time.
ASSEMBLY_EDIT_BUDGET = 20
# …of any one component, at most this share of the parts in it.
ASSEMBLY_EDIT_SHARE = 0.25
# Below this many parts a share is meaningless - a two-brick component would
# be down to half a brick - so small components get a flat allowance instead.
ASSEMBLY_EDIT_FLOOR = 2

_FILE_LINE = re.compile(r"^\s*0\s+FILE\s+(.+?)\s*$", re.IGNORECASE)


def _is_part(line):
    return line.strip().startswith("1 ")


def _mpd_blocks(lines):
    """``[(name, first_line, last_line)]`` for one file, 1-indexed.

    An assembled scene is an MPD: the scene itself, then one block per
    subconstruction. A plain .ldr with no ``0 FILE`` in it is a single block -
    which is what the guard sees if it is ever pointed at a component directly.
    """
    marks = [(i + 1, found.group(1).strip())
             for i, line in enumerate(lines)
             if (found := _FILE_LINE.match(line))]
    if not marks:
        return [("the model", 1, len(lines))]

    blocks = []
    for n, (start, name) in enumerate(marks):
        end = marks[n + 1][0] - 1 if n + 1 < len(marks) else len(lines)
        blocks.append((name, start, end))
    if marks[0][0] > 1:
        blocks.insert(0, ("the file header", 1, marks[0][0] - 1))
    return blocks


def _block_at(blocks, line):
    for name, start, end in blocks:
        if start <= line <= end:
            return name, start, end
    return "the model", 1, line


def _assembly_guard(original, planned, state):
    """Is this batch of edits joining the scene, or rewriting a piece of it?

    Returns an error dict to refuse with, or None to let the edit through. The
    refusals name the tool that *would* have been right, because an assembly
    pass told only "no" spends its remaining steps trying the same thing.
    """
    blocks = _mpd_blocks(original)
    parts_in = {name: sum(1 for ln in original[start - 1:end] if _is_part(ln))
                for name, start, end in blocks}

    touched = 0
    removed_from = {}
    for edit in planned:
        gone = [ln for ln in original[edit["start"] - 1:edit["end"]] if _is_part(ln)]
        added = [ln for ln in edit["new"] if _is_part(ln)]
        touched += len(gone) + len(added)
        if gone:
            name, _, _ = _block_at(blocks, edit["start"])
            removed_from[name] = removed_from.get(name, 0) + len(gone)

    # Nothing structural: renaming a block, fixing a comment, adding a STEP.
    # Never worth refusing, and never worth charging for either.
    if touched == 0:
        return None

    if touched > ASSEMBLY_EDIT_PARTS:
        return {
            "error": f"that edit changes {touched} parts, and assembly may "
                     f"change {ASSEMBLY_EDIT_PARTS} in one call",
            "why": "You are composing finished objects, not rebuilding them. "
                   "An edit this size is a redesign of a component that was "
                   "already built and validated.",
            "hint": "If two objects are in the wrong places, `move_submodel` "
                    "and `rotate_submodel` move whole objects and cost you "
                    "nothing. Keep edit_model for the join itself: the few "
                    "parts that actually clash, or the plate that ties two "
                    "objects together.",
        }

    spent = getattr(state, "parts_edited", 0)
    if spent + touched > ASSEMBLY_EDIT_BUDGET:
        return {
            "error": f"assembly has already changed {spent} parts of this "
                     f"scene, and the pass may change {ASSEMBLY_EDIT_BUDGET}",
            "why": "Past this the pass is no longer assembling a scene out of "
                   "finished objects, it is building a new one.",
            "hint": "Place what you have with `move_submodel` and "
                    "`rotate_submodel`, validate, and finish. If a component "
                    "is genuinely wrong, say so in your summary - that is a "
                    "report the user can act on, and rebuilding it here is not.",
        }

    for name, gone in removed_from.items():
        allowed = max(ASSEMBLY_EDIT_FLOOR,
                      int(parts_in.get(name, 0) * ASSEMBLY_EDIT_SHARE))
        if gone > allowed:
            return {
                "error": f"that edit takes {gone} of the "
                         f"{parts_in.get(name, 0)} parts out of `{name}`, and "
                         f"assembly may take out {allowed}",
                "why": f"`{name}` is a finished subconstruction. Removing that "
                       f"much of it is dismantling it, which is not what this "
                       f"pass is for.",
                "hint": "Move it instead - `move_submodel` shifts the whole "
                        "object and never breaks it. Take out only the parts "
                        "that are physically in the way of another object.",
            }

    return None


def _pairs(items):
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            yield a, b


def _as_lines(value):
    """New text as a list of lines, given as one string or as a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(str(item).splitlines() or [""])
        return out
    return str(value).splitlines()


def _numbered(lines, start, end):
    """A slice of a file with its line numbers, clipped to what exists."""
    start, end = max(1, start), min(len(lines), end)
    return "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(start, end + 1))


def _regions(planned, lines):
    """Each edited region as it stands now, with its new line numbers.

    So a second edit in the same repair can be written against real numbers
    rather than against arithmetic the model did in its head.
    """
    shift, regions = 0, []
    for edit in sorted(planned, key=lambda e: e["start"]):
        first = edit["start"] + shift
        last = first + len(edit["new"]) - 1
        shift += len(edit["new"]) - (edit["end"] - edit["start"] + 1)
        if not edit["new"]:
            # A deletion leaves nothing to point at; show where it closed up.
            regions.append(_numbered(lines, first - EDIT_CONTEXT_LINES,
                                     first + EDIT_CONTEXT_LINES - 1))
            continue
        regions.append(_numbered(lines, first - EDIT_CONTEXT_LINES,
                                 last + EDIT_CONTEXT_LINES))
    return regions


def _quick_render(target, path, state=None):
    """Render a freshly written model. Never fails the write."""
    if not render.available():
        return {"rendered": False,
                "render_note": "LeoCAD is not installed, so there is no picture"}
    try:
        project = _project_of(path)
        images = render.render_model_file(target, project=project)
    except Exception as exc:
        return {"rendered": False, "render_note": f"could not render ({exc})"}

    if state is not None:
        state.record_render(path, [str(p) for _, p in images])
    return {"rendered": True,
            "render_views": [view for view, _ in images],
            "_images": _keep_images(path, images),
            "render_note": ("The user can see this now. validate_model when "
                            "you want it described back to you.")}


def _keep_images(path, images=(), sheet=None, reference_image=None):
    """Copy what was just rendered or looked at into the run's trace archive.

    Renders live at stable filenames and are overwritten by the next write, so
    a trace read afterwards would show the latest model beside every decision
    that was ever made. Copies land beside the trace instead, under ``_images`` -
    a key the agent loop strips out before the result reaches the model,
    because the builder is text-only and a list of filenames it cannot open is
    tokens spent on nothing. The picture is for the person reading the run.
    """
    project = _project_of(path)
    kept = []
    for view, image in images or ():
        record = trace.keep_image(project, image, kind="render", view=view)
        if record:
            kept.append(record)
    if sheet:
        record = trace.keep_image(project, sheet, kind="sheet",
                                  label="contact sheet")
        if record:
            kept.append(record)
    # One reference picture or four - see reference.py. All of them are kept:
    # a comparison made against four and shown beside one is a trace of
    # something that did not happen.
    for picture in ([reference_image]
                    if isinstance(reference_image, (str, Path))
                    else list(reference_image or ())):
        record = trace.keep_image(project, picture, kind="reference",
                                  label="the reference picture")
        if record:
            kept.append(record)
    return kept


def _project_of(path):
    """Which project a model path belongs to, for the renders directory."""
    parts = Path(path).parts
    if "projects" in parts:
        index = parts.index("projects")
        if index + 1 < len(parts):
            return parts[index + 1]
    return Path(path).parent.name or "model"


def _describe_image(request=None, image_id=None, state=None):
    """Describe the reference picture, or pictures, attached to this project.

    All of them, in one call, unless one is named. A project may hold up to
    four and every one of them is the specification - see reference.py.
    """
    if state is None or not state.project:
        return {"error": "there is no project to look for a reference image in"}

    if image_id:
        one = reference.resolve(state.project, image_id)
        records = [one] if one else []
    else:
        records = reference.active(state.project)
    if not records:
        return {"error": "no reference image is attached to this project",
                "hint": "Build from the written request instead."}

    pictures = reference.paths(records, state.project)
    if not pictures:
        return {"error": "the reference image is recorded but missing on disk"}

    record = records[0]
    ids = [r.get("image_id") for r in records]

    # What is being described, kept with the description. A trace that says
    # "it read the picture as a red tractor" is worth much less than one that
    # puts the picture next to the reading.
    shown = _kept_reference(state.project, pictures)

    # Described once. A picture does not change between steps, and the
    # description is a vision call.
    stored = reference.described(records)
    if stored:
        return {"image_id": record.get("image_id"), "images": len(pictures),
                "description": stored, "cached": True, "_images": shown,
                "note": _REFERENCE_NOTE}

    try:
        description = render.describe(pictures,
                                      request=request or state.requirements)
    except render.NotAvailable as exc:
        return {"error": str(exc), "_images": shown,
                "hint": "Build from the written request instead - do not guess "
                        "at what the picture shows."}

    # A reply that did not come back in the shape asked for was almost always
    # cut off part-way. It is worth handing over - half a description beats
    # none - but it must NOT be stored: the stored one is never asked for
    # again, so caching a truncated answer means the project is stuck with it
    # for good.
    if description.get("unstructured"):
        return {"image_id": record.get("image_id"), "description": description,
                "partial": True, "_images": shown,
                "note": "This description was cut short, so it is incomplete "
                        "and has NOT been kept. Build from what is here, and "
                        "call describe_image again for the rest - it will ask "
                        "afresh rather than repeating this."}

    reference.set_description(state.project, ids, description)
    state.reference_description = description
    return {"image_id": record.get("image_id"), "images": len(pictures),
            "description": description,
            "_images": shown, "note": _REFERENCE_NOTE}


def _kept_reference(project, image, label="the reference picture"):
    """The reference pictures, archived for the trace. See ``_keep_images``.

    ``image`` is one path or several: a project may have up to four reference
    pictures, and a trace that shows one of them is a trace of a reading that
    did not happen.
    """
    many = [image] if isinstance(image, (str, Path)) else list(image or ())
    kept = [trace.keep_image(project, p, kind="reference", label=label)
            for p in many]
    return [r for r in kept if r]


def _ask_vision_model(questions=None, purpose=None, image_id=None, state=None):
    """Put a prepared set of questions about the reference picture.

    The budget lives on the run's ledger rather than here (see
    ``RunState.MAX_ASKS``): ten sets of questions for the whole run, spent as
    the build needs them. Two things are checked before one is spent, because
    both would spend it on nothing - that the picture has been described
    already, and that these questions have not been answered before.
    """
    if state is None or not state.project:
        return {"error": "there is no project to look for a reference image in"}

    questions = [str(q).strip() for q in (questions or [])
                 if isinstance(q, (str, int, float)) and str(q).strip()]
    if not questions:
        return {"error": "give at least one question",
                "hint": "Ask about what you are otherwise going to guess: a "
                        "count, a colour, which part sits on which."}
    if len(questions) > MAX_QUESTIONS:
        return {"error": f"{len(questions)} questions, and you may ask "
                         f"{MAX_QUESTIONS} at a time",
                "hint": "Keep the ones whose answers change what you build; "
                        "drop the rest. Nothing is spent - ask again with the "
                        "shorter list."}

    if image_id:
        one = reference.resolve(state.project, image_id)
        records = [one] if one else []
    else:
        records = reference.active(state.project)
    if not records:
        return {"error": "no reference image is attached to this project",
                "hint": "There is nothing to ask about. Build from the written "
                        "request instead."}
    pictures = reference.paths(records, state.project)
    if not pictures:
        return {"error": "the reference image is recorded but missing on disk"}

    record = records[0]
    ids = [r.get("image_id") for r in records]
    shown = _kept_reference(state.project, pictures)

    # The description is free and covers most of what gets asked. Spending the
    # one set of questions on things it would have answered is the failure
    # this refusal exists to prevent - and it costs a step to clear, not a
    # credit.
    if not record.get("description"):
        return {"error": "this picture has not been described yet",
                "hint": "Call describe_image first. It is free, it answers "
                        "most of what you are about to ask, and you will know "
                        "far better what is actually still open. Your "
                        "questions are still yours to spend afterwards."}

    known = reference.answered(record)
    fresh = [q for q in questions if reference.normalize_question(q) not in known]
    if not fresh:
        return {"answers": [known[reference.normalize_question(q)] for q in questions],
                "cached": True, "_images": shown,
                "note": "These were asked about this picture before, so this "
                        "cost you nothing. Your questions are still available.",
                "spent": False}

    if not state.may_ask():
        return {"error": f"you have put {len(state.asks)} sets of questions to "
                         f"this picture, which is the limit for one build",
                "already_asked": state.asked_questions(),
                "hint": "Build with what you have. validate_model compares "
                        "what you built against this picture for free, and "
                        "that comparison is worth more now than another "
                        "question about a model you have not written yet.",
                "spent": False}

    try:
        answer = render.ask(pictures, fresh,
                            request=purpose or state.requirements,
                            description=record.get("description"))
    except render.NotAvailable as exc:
        # Nothing was spent: a question that could not be put was not asked.
        return {"error": str(exc),
                "hint": "Build from the description you already have - do not "
                        "guess at what the picture shows."}

    entries = _qa_entries(fresh, answer)
    reference.add_qa(state.project, ids, entries)
    state.record_ask(fresh, answer)

    result = {"image_id": record.get("image_id"),
              "answers": answer.get("answers") or entries,
              "spent": True, "asks_left": state.asks_left(), "_images": shown,
              "note": _ASK_NOTE}
    if answer.get("also_worth_knowing"):
        result["also_worth_knowing"] = answer["also_worth_knowing"]
    if len(fresh) < len(questions):
        result["already_known"] = [known[reference.normalize_question(q)]
                                   for q in questions
                                   if reference.normalize_question(q) in known]
    if answer.get("text"):
        # It answered in prose rather than the shape that was asked for.
        result["answers"] = answer["text"]
        result["note"] = ("This came back as prose rather than one answer per "
                          "question. Read it as a comment. " + _ASK_NOTE)
    return result


def _qa_entries(questions, answer):
    """Question/answer pairs to remember, however the model shaped its reply."""
    rows = answer.get("answers")
    if isinstance(rows, list) and len(rows) == len(questions):
        return [{"question": q,
                 "answer": (r.get("answer") if isinstance(r, dict) else str(r)),
                 "visible": (r.get("visible") if isinstance(r, dict) else None)}
                for q, r in zip(questions, rows)]
    # A reply that did not line up with the questions is still worth keeping,
    # but it cannot be split between them: attach the whole of it to each,
    # rather than guessing which sentence belonged to which question.
    whole = answer.get("text") or json.dumps(rows, ensure_ascii=False,
                                             default=str)
    return [{"question": q, "answer": whole} for q in questions]


_ASK_NOTE = (
    "Build with these answers now: treat them as the specification alongside "
    "the description, and where an answer says something was not visible, "
    "that is yours to decide. Ask again whenever the picture is what you are "
    "actually missing - you have questions left - but a question you could "
    "answer by looking at your own render is a question worth not spending."
)


_REFERENCE_NOTE = (
    "This is the specification. Build what it describes - the composition, the "
    "colours and the proportions especially - rather than what you would have "
    "designed from the text alone. validate_model will compare what you build "
    "against this picture and tell you where they differ."
)


def _measured_facts(report):
    """What the geometry checker established, phrased for the vision model.

    The critic is about to be asked what is wrong with a picture. Three of the
    things it will reach for - is this connected, is anything floating, how big
    is it - have already been computed exactly from the coordinates, and they
    are the three a vision model is least able to judge from a downscaled tile.
    Handing them over is what stops the critique inventing them.

    Only ever states what was actually measured. A check that did not run says
    nothing, because the failure this is here to prevent is a confident sentence
    with nothing behind it, and producing one of those in the course of fixing
    them would be its own joke.
    """
    if not isinstance(report, dict) or not report.get("passed"):
        return None

    connectivity = report.get("connectivity") or {}
    facts = []

    # geometry.measure returns width/depth in studs and height in bricks, which
    # is the way a builder says it and the way the critic should hear it.
    studs = (report.get("size") or {}).get("size_studs") or {}
    parts = report.get("parts")
    if studs.get("width") and studs.get("depth"):
        facts.append(
            f"it is {studs['width']} studs wide by {studs['depth']} deep and "
            f"{studs.get('height_bricks')} bricks tall"
            + (f", built from {parts} parts" if parts else ""))
    elif parts:
        facts.append(f"it has {parts} parts")

    # Only when the question was actually put - see validation, where an empty
    # list means "clean" or "never asked" depending on this.
    scope = connectivity.get("objects_checked")
    if scope == "whole" and not connectivity.get("objects_in_pieces"):
        facts.append("EVERY PART IS JOINED TO THE REST OF THE MODEL - this is "
                     "one connected build, measured, not estimated")
    elif scope == "blocks" and not connectivity.get("objects_in_pieces"):
        facts.append("each separate object in this scene is internally "
                     "connected - measured, not estimated")

    if not connectivity.get("floating"):
        facts.append("nothing is floating: every part has a path of support "
                     "down to the ground")
    if not (report.get("collision") or {}).get("overlapping"):
        facts.append("no two parts share solid plastic")

    if not facts:
        return None
    return ("Measured facts about this model, computed from its coordinates. "
            "These are exact. Where one of them contradicts what you think you "
            "see, it is right and you are looking at a small picture:\n"
            + "\n".join(f"- {fact}" for fact in facts))


def _reconcile(verdict, report):
    """Set the critique against the measurements, and mark what cannot both hold.

    The critic runs only on a model that already passed the grid check, so by
    the time it speaks, connectivity is not an open question - it is a fact on
    file. When the two disagree about it, one of them is wrong in a way we can
    name, and saying nothing means the builder acts on whichever it read last.

    Only connectivity is arbitrated, and only in the direction the evidence
    supports. Everything else the critic reports - proportion, character, a
    missing wheel, a figure with no arm - is exactly what it is for, and no
    measurement here has an opinion about any of it.
    """
    if not isinstance(verdict, dict) or not isinstance(report, dict):
        return verdict

    connectivity = report.get("connectivity") or {}
    scope = connectivity.get("objects_checked")
    measured_whole = (scope in ("whole", "blocks")
                      and not connectivity.get("objects_in_pieces"))

    if verdict.get("one_build") is False and measured_whole:
        # It saw two lumps. The geometry says they are joined. Both can be true
        # at once and usually are: a build can be one connected model and still
        # read as two, which is a real fault about spacing and a different one
        # from the fault the critic named.
        verdict["one_build_note"] = (
            "The geometry checker measured this model as one connected build - "
            "every part is joined to the rest. So this is NOT a disconnection, "
            "and nothing needs reattaching. What the critic saw is real but is "
            "a different fault: the model *reads* as separate lumps. Close the "
            "gap visually - bring the masses together, or bridge them with "
            "something that spans the join - and do not go looking for parts "
            "that have come adrift, because there are none.")
        verdict["one_build_measured"] = True
        verdict["separate_pieces_note"] = (
            "named by eye, and contradicted by the measurement above")
    elif connectivity.get("objects_in_pieces") and verdict.get("one_build") is True:
        # The other way round, and rarer: the picture hid it. This one the
        # measurement simply wins, because a gap can be behind a nearer part.
        names = ", ".join(str(e.get("object")) for e in
                          connectivity["objects_in_pieces"][:3])
        verdict["one_build_note"] = (
            f"The critic saw one build; the geometry checker did not. {names} "
            f"is in more than one piece with nothing joining them. The "
            f"measurement is exact and a gap can hide behind a nearer part, so "
            f"fix what `objects_in_pieces` lists.")
        verdict["one_build"] = False
        verdict["one_build_measured"] = True
    return verdict


def _render_model(path, subject=None, question=None, critique=True, state=None):
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}

    subject = subject or (state.subject if state else None)
    requirements = state.requirements if state else None
    # What was measured a moment ago, on this same file, by the call that is
    # about to look at it. Handed to the critic so it does not have to guess at
    # the half of this report that is already known exactly.
    report = state.validation_of(path) if state is not None else None

    try:
        images, sheet, verdict, note = render.look(
            target, subject=subject, requirements=requirements,
            question=question, project=_project_of(path),
            measured=_measured_facts(report),
            # What this was meant to look like. The critic judges the model
            # against what it was asked to be, and until it was given this it
            # was judging against its own idea of the subject.
            brief=(state.brief if state is not None else None),
        ) if critique is not False else (
            render.render_model_file(target, project=_project_of(path)),
            None, None, "critique was not requested")
    except render.NotAvailable as exc:
        return {"error": str(exc)}

    # Before it is recorded or shown: the critique against the measurements.
    # A contradiction settled after the builder has already read the critique
    # is a contradiction settled too late.
    if verdict:
        verdict = _reconcile(verdict, report)

    if state is not None:
        state.record_render(path, [str(p) for _, p in images],
                            sheet=str(sheet) if sheet else None)
        if verdict:
            state.record_critique(path, verdict)

    result = {
        "views": [{"view": view, "image": str(Path(p).relative_to(PROJECT_ROOT))}
                  for view, p in images],
        # what the critic was actually shown, kept so the trace can show it too
        "_images": _keep_images(path, images, sheet=sheet),
    }
    if sheet:
        result["contact_sheet"] = str(Path(sheet).relative_to(PROJECT_ROOT))
    if verdict:
        result["seen"] = verdict
        note = ("This is what the model actually looks like. Each issue comes "
                "with the change that resolves it - apply them all in one "
                "rewrite, keeping everything listed under `good` as it is, "
                "then validate_model once more. Treat them as real: you cannot "
                "see the model and this is the only report you get. If the "
                "issues list is empty and the verdict says it is finished, "
                "stop changing it.")
        # The one fault worth saying twice, because it used to be the one the
        # builder could not detect any other way. It is measured now - so this
        # shouts only where the measurement agrees, and defers to `_reconcile`
        # where it does not. Shouting over a measurement is how a builder gets
        # sent to reattach parts that were never loose.
        if verdict.get("one_build") is False and not verdict.get("one_build_measured"):
            stray = ", ".join(verdict.get("separate_pieces") or []) or "part of it"
            note = (f"THIS IS NOT ONE BUILD. {stray} is standing apart from the "
                    f"main model instead of being attached to it. Unless that "
                    f"piece is genuinely a separate object - a car beside a "
                    f"house, a minifigure - this is wrong and must be fixed "
                    f"before anything else: move it onto the build so it seats "
                    f"on real studs, then validate_model again. " + note)
        elif verdict.get("one_build_note"):
            note = verdict["one_build_note"] + " " + note
        result["note"] = note
    if note:
        result["critique_note"] = note

    # The second look, and the one that decides whether the build is what was
    # actually asked for: the renders against the picture the user attached.
    if sheet and critique is not False:
        compared = _compare_reference(sheet, path, subject, state)
        # merged rather than overwritten: both halves of this call have
        # something to show, and `update` would drop the renders for the
        # reference picture
        result["_images"] = (result.get("_images") or []) + \
            (compared.pop("_images", None) or [])
        result.update(compared)
    return result


def _compare_reference(sheet, path, subject, state):
    """Judge the renders against the project's reference images, if any."""
    if state is None or not state.project:
        return {}
    records = reference.active(state.project)
    if not records:
        return {}
    images = reference.paths(records, state.project)
    if not images:
        return {}

    # The pictures it was held against, kept whether or not the comparison came
    # back: "compared to what" is worth seeing either way.
    shown = _keep_images(path, reference_image=images)

    try:
        verdict = render.compare(
            sheet, images, subject=subject,
            description=(reference.described(records)
                         or state.reference_description))
    except render.NotAvailable as exc:
        return {"_images": shown,
                "reference_note": f"could not compare against the reference ({exc})"}

    if state is not None:
        state.record_reference_check(path, verdict)

    matched = verdict.get("matches")
    out = {"reference_check": verdict, "_images": shown}

    # The changes are the point of this call, so they are lifted out of the
    # verdict and put where they cannot be skimmed past.
    changes = [c for c in (verdict.get("changes") or []) if isinstance(c, dict)]
    if changes:
        out["changes_to_make"] = changes

    if matched is False:
        worth_it = [c for c in changes
                    if str(c.get("severity", "")).lower() in ("fatal", "major")]
        out["reference_note"] = (
            "Not there yet - the run cannot finish until it reads as the "
            "picture, and `changes_to_make` is how to get there. Work down it "
            "in order: it is sorted by how much each change buys, and "
            "composition and colour move the needle further than detail."
            + (f" {len(worth_it)} of the {len(changes)} are the ones that "
               f"matter." if worth_it else "")
            + " Keep everything listed under `keep` exactly as it is, apply the "
              "changes in one rewrite, then validate_model again. You cannot see "
              "the picture and this can, so take its word for what is "
              "different - but the how is yours: build it with the parts you "
              "think fit.")
        if verdict.get("closeness"):
            out["reference_note"] = (f"{verdict['closeness'].strip().rstrip('.')}. "
                                     + out["reference_note"])
    elif matched is True:
        out["reference_note"] = (
            "It reads as the reference. Minor differences are what LEGO does to "
            "a photograph and are not worth another round - stop here unless "
            "something else is wrong.")
    return out


def _measure_model(path):
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    result = geometry.measure(target)
    if "error" not in result:
        result["note"] = ("-Y is up, so top_y is a smaller number than "
                          "ground_y. Sizes are the model's full extent, "
                          "including studs.")
    return result


def _scene_text(path):
    target = _resolve(path)
    if not target.is_file():
        return None, {"error": f"no such file: {path}"}
    return target.read_text(encoding="utf-8", errors="replace"), None


def _arranged(path, text, report, state=None):
    """Write a rearranged scene back, and say what it holds now."""
    written = _write_file(path, text, state=state)
    if "error" in written:
        return written
    return {**report,
            "scene": arrange.summary(text),
            "lines": written.get("lines"),
            "next": "validate_model on the scene to see whether that cleared it."}


def _move_submodel(path, submodel, dx=0, dy=0, dz=0, state=None):
    """Shift one whole subconstruction within an assembled scene."""
    text, error = _scene_text(path)
    if error:
        return error
    moved, report = arrange.move(text, submodel, dx, dy, dz)
    if moved is None:
        return {**report, "scene": arrange.summary(text)}
    return _arranged(path, moved, report, state=state)


def _rotate_submodel(path, submodel, degrees=90, axis="y", state=None):
    """Turn one whole subconstruction, about its own centre."""
    text, error = _scene_text(path)
    if error:
        return error
    turned, report = arrange.rotate(text, submodel, degrees, axis)
    if turned is None:
        return {**report, "scene": arrange.summary(text)}
    return _arranged(path, turned, report, state=state)


def _assemble_model(path, components, title=None, state=None):
    if not components:
        return {"error": "no components given"}

    resolved = []
    for spec in components:
        if not isinstance(spec, dict) or not spec.get("file"):
            return {"error": "every component needs a 'file'"}
        source = _resolve(spec["file"])
        if not source.is_file():
            return {"error": f"no such subbuild file: {spec['file']}",
                    "hint": "Build and write each subconstruction before "
                            "assembling them."}
        resolved.append({**spec, "file": str(source)})

    target = _resolve(path)
    text, report = assembly.compose(
        resolved, title=title or "Scene", main_name=Path(target).name)
    if text is None:
        return report

    written = _write_file(path, text, state=state)
    if "error" in written:
        return written

    return {**written, **report,
            "next": "Now validate_model on the assembled scene."}


def _finish(summary, give_up=False, blocked_by=None, state=None):
    """End the run - if the run has actually done the work.

    Without a state to check against there is nothing to enforce, so the call
    is simply accepted: a harness that did not set up a gate did not ask for
    one.
    """
    summary = (summary or "").strip()
    if not summary:
        return {"finished": False,
                "why": "finish needs a summary - say what you built and what "
                       "the checks reported"}

    if state is None:
        return {"finished": True, "summary": summary, "gave_up": bool(give_up)}

    if give_up:
        if not (blocked_by or "").strip():
            return {"finished": False,
                    "why": "give_up needs blocked_by: say specifically what "
                           "stopped you, so the user knows what went wrong"}
        # Giving up covers a build that cannot be made. It does not cover a
        # part in the wrong place: that is a number to round onto the lattice,
        # and a run allowed to walk away from it hands back a model that no
        # amount of real bricks will reproduce.
        refused = state.refuse_give_up()
        if refused is not None:
            return refused
        return state.accept(summary, gave_up=True, blocked_by=blocked_by)

    # A build with a checklist does not end by being declared finished. Refused
    # here rather than only in the gate, so the answer names the actual rule
    # instead of listing whichever requirement happens to be outstanding - the
    # builder has to stop trying to end the run and go back to building.
    # See requirements.py and LDrawAgent._requirements_gate.
    if runstate.requirements_module.items(getattr(state, "requirements", None)):
        result = state.requirements_result()
        met = len((result or {}).get("met") or [])
        total = len(runstate.requirements_module.items(state.requirements))
        return {
            "finished": False,
            "why": ("finish does not end this run. Your requirements do, and "
                    "they are checked for you every time you call "
                    "validate_model - the run stops by itself when all of them "
                    "are met."),
            "requirements_met": f"{met} of {total}",
            "do_next": ("carry on building what the outstanding requirements "
                        "ask for, then validate_model. If something genuinely "
                        "stops you, call finish with give_up=true and say what."),
        }

    ok, problems, next_step = state.gate()
    if not ok:
        return state.reject(problems, next_step)
    return state.accept(summary)


def _submodel_range(lines, wanted):
    """The line range of one named block of an MPD, or an error naming them all.

    Matched on any part of the name, case-insensitively, because the names in
    a real set are filenames - "40440 - Puppy.ldr" - and asking for "puppy" is
    what a reader would do. An unambiguous prefix is enough; when several match
    the shortest wins, which is the plain block rather than a variant of it.
    """
    blocks = []
    for number, line in enumerate(lines, start=1):
        match = _FILE_LINE.match(line)
        if match:
            blocks.append((match.group(1), number))
    if not blocks:
        return None, {"error": "this file has no submodels - it is a single "
                               "model, so read it by line number instead"}

    needle = str(wanted).strip().lower()
    hits = [b for b in blocks if needle in b[0].lower()]
    if not hits:
        return None, {"error": f"no submodel matching '{wanted}'",
                      "submodels": [b[0] for b in blocks]}
    name, start = min(hits, key=lambda b: len(b[0]))
    after = [s for _, s in blocks if s > start]
    end = (min(after) - 1) if after else len(lines)
    # "wing" against `left wing.ldr` and `right wing.ldr` picks one of them,
    # and the agent has to be told which - a block quietly chosen out of three
    # is read as *the* answer, and the geometry in it belongs to a part of the
    # set nobody asked about.
    others = [n for n, _ in hits if n != name]
    return {"name": name, "start": start, "end": end, "others": others}, None


def _read_lines(lines, start_line, end_line, submodel=None, cap=None):
    """A numbered window on a file: a submodel if one was named, else a range.

    The numbers are the whole file's, never the window's. They are what
    edit_model takes, and a listing that renumbered from 1 would be a listing
    every line number in it was wrong.
    """
    picked = None
    if submodel:
        picked, error = _submodel_range(lines, submodel)
        if error:
            return None, error
        start, end = picked["start"], picked["end"]
        # `last` is what "there is more" is measured against: the end of the
        # submodel when one was named, the end of the file otherwise. Without
        # this a submodel longer than the cap came back truncated and looking
        # complete, which is the worst way to be wrong about a reference - the
        # agent reads two thirds of a wing and builds it as if that were all.
        last = end
        # A start_line inside the block pages through it, so a long submodel is
        # read the same way a long file is.
        if start_line and int(start_line) > start:
            start = min(int(start_line), end)
    else:
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line or len(lines)))
        last = len(lines)

    if start > len(lines):
        return None, {"error": f"line {start} is past the end of this file, "
                               f"which has {len(lines)} lines"}
    if cap:
        end = min(end, start + cap - 1)
    end = max(start, min(end, last))

    body = "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(start, end + 1))
    out = {"total_lines": len(lines), "shown_lines": [start, end], "content": body}
    if picked:
        out["submodel"] = picked["name"]
        out["submodel_lines"] = [picked["start"], picked["end"]]
        if picked["others"]:
            out["also_matched"] = picked["others"]
            out["note"] = ("Several submodels matched that name; this is the "
                           "shortest. The others are listed under "
                           "`also_matched`.")
    if end < last:
        out["more"] = (f"{last - end} more lines"
                       f"{' in this submodel' if picked else ''}; call again "
                       f"with start_line={end + 1}")
    return out, None


def _read_project_file(path, start_line=1, end_line=None, submodel=None):
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window, error = _read_lines(lines, start_line, end_line, submodel)
    if error:
        return error
    return {"path": str(target.relative_to(PROJECT_ROOT)), **window}


def _read_source(source, start_line=1, end_line=None, submodel=None):
    """Read LDraw source, wherever it lives.

    Three tools used to do this - one for the model being built, one for an
    official set, one for a saved creation - with identical parameters and
    identical output, differing only in how the thing was named. So the name is
    the argument now, and there is one tool.
    """
    text = str(source or "").strip()
    kind, _, rest = text.partition(":")
    rest = rest.strip()
    if kind.lower() == "set" and rest:
        return _read_set_model(rest, start_line, end_line, submodel)
    if kind.lower() == "creation" and rest:
        return _read_creation(rest, start_line, end_line, submodel)
    if kind.lower() in ("set", "creation"):
        return {"error": f"'{text}' names no {kind.lower()}: "
                         f"write it as '{kind.lower()}:<name>'"}
    return _read_project_file(text, start_line, end_line, submodel)


# What each corpus takes, so a filter meant for one is not silently handed to
# another - asking for `theme` among your own notes is a mistake worth naming.
_REFERENCE_FILTERS = {
    "sets": ("theme", "year_min", "year_max", "min_pieces", "max_pieces"),
    "creations": ("tag", "validated_only", "min_pieces", "max_pieces"),
}


def _search_reference(kind=None, query=None, like=None, max_results=8,
                      theme=None, year_min=None, year_max=None,
                      min_pieces=None, max_pieces=None, tag=None,
                      validated_only=None):
    """Search the corpora that are not the parts catalogue.

    Sets and saved creations were separate tools doing one thing - a semantic
    search with a few filters - and between them they accounted for a twentieth
    of the calls a run makes. One tool, one `kind`.

    Notes used to be a third kind here. Nothing writes them any more (there is
    no `add_note`), and the ones that exist reach a build the way they are
    actually useful: attached to the part they are about, in
    `get_part_details`. A search for them was a call spent on a corpus the
    agent could no longer add to.
    """
    kind = (kind or "").strip().lower()
    if kind not in _REFERENCE_FILTERS:
        return {"error": "kind must be one of sets, creations"}

    given = {"theme": theme, "year_min": year_min, "year_max": year_max,
             "min_pieces": min_pieces, "max_pieces": max_pieces, "tag": tag,
             "validated_only": validated_only}
    stray = [k for k, v in given.items()
             if v is not None and k not in _REFERENCE_FILTERS[kind]]
    if stray:
        return {"error": f"{', '.join(stray)} does not apply to {kind}; "
                         f"{kind} takes {', '.join(_REFERENCE_FILTERS[kind])}"}

    if kind == "sets":
        if like:
            return _find_similar_sets(like, theme=theme, min_pieces=min_pieces,
                                      max_pieces=max_pieces,
                                      max_results=max_results)
        if not query:
            return {"error": "searching sets needs a query, or `like` with a "
                             "set number to find others close to"}
        return _search_sets(query, theme=theme, year_min=year_min,
                            year_max=year_max, min_pieces=min_pieces,
                            max_pieces=max_pieces, max_results=max_results)

    if like:
        return {"error": "`like` only applies to kind='sets'"}

    if not query:
        return {"error": "searching your creations needs a query"}
    return _search_creations(query, tag=tag, validated_only=validated_only,
                             min_pieces=min_pieces, max_pieces=max_pieces,
                             max_results=max_results)


def _ask_about_image(questions=None, request=None, purpose=None,
                     image_id=None, state=None):
    """Look at the reference picture: describe it, or answer questions about it.

    One call either way. They were two tools because they are two prompts, but
    to a builder they are one question - *what is in the picture* - and which
    of the two it wants is exactly whether it has questions yet.
    """
    asked = [q for q in (questions or []) if str(q).strip()]
    if asked:
        return _ask_vision_model(questions=asked, purpose=purpose,
                                 image_id=image_id, state=state)
    return _describe_image(request=request, image_id=image_id, state=state)


# How many repair passes `validate_model` runs before it hands what is left to
# the model. Each pass is capped inside autofix, so a badly broken draft needs
# several; past this the model is not one with a few slips in it.
AUTOFIX_PASSES = 6


def _fix_collision(path):
    """Nudge every part a legal move would get out of trouble."""
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    report = autofix.fix(target)
    if "error" in report:
        return report
    report["file"] = str(target.relative_to(PROJECT_ROOT))
    report["note"] = _autofix_note(report)
    return report


def _autofix_note(report):
    """What the model has to take away from a round of auto-fixing."""
    moved, left = report.get("moved", 0), report.get("remaining", 0)
    # Overlaps the pass ran out of rounds before reaching, as against ones it
    # looked at and refused. Only the refused ones have a reason to go and
    # read, so only they are worth pointing at unfixed_parts for.
    missed = report.get("not_reached", 0)
    listed = max(0, left - missed)

    if not moved and not left:
        return "nothing was inside anything else - there was nothing to fix"

    parts = []
    if moved:
        parts.append(
            f"{moved} part{'' if moved == 1 else 's'} slid back onto the grid "
            f"for you. Those lines still have the numbers they had, but new "
            f"coordinates - read_model before you edit them")
    if listed:
        parts.append(
            f"{listed} overlap{'' if listed == 1 else 's'} could not be fixed "
            f"by moving anything: see unfixed_parts, each with the reason. "
            f"These are yours to fix, and each needs a real decision - a "
            f"different part, a different place in the build, a whole "
            f"sub-assembly moved, or one of the two lines deleted")
    if missed:
        # One pass is capped. Saying so is the difference between "you are
        # clear" and "I stopped looking", and only one of those is true here.
        parts.append(
            f"{missed} more {'was' if missed == 1 else 'were'} never looked "
            f"at - one pass repairs a limited number, so call fix_collision "
            f"again once you have dealt with what is listed")
    if not left:
        parts.append("nothing is inside anything else any more")
    return ". ".join(parts) + "."


def _validate_model(path, tolerance=2.0, question=None, state=None,
                    look=None, grid_only=False):
    """Check the model on the grid, and look at it. Always both.

    These were two tools, and they were two tools that always had to be called
    together - the grid says whether a build is legal and says nothing at all
    about whether a car looks like a car, so a run that skipped the second was
    a run that finished a model nobody had seen. `finish` refused both ways,
    which is the shape of a thing that was one step all along.

    So the looking is no longer optional and no longer conditional. There were
    two ways to skip it and both were wrong:

    * **``look=false``**, offered for the middle of a repair round. What it
      actually bought was a builder that could decide it did not want to be
      seen, on the turn where being seen mattered most - and every skipped
      render is a render the *user* did not get either, since the pictures in
      the trace and on the workbench are the same pictures.
    * **The grid check failing**, on the reasoning that a model with parts
      inside each other is going to be rewritten anyway. That was the more
      expensive of the two. An overlap is one line out of a hundred; the shape
      those hundred lines make is a separate fact, it is still true while the
      overlap is there, and withholding it meant the builder repaired the
      arithmetic blind and only found out on the *next* iteration that the
      thing it had spent the repair on did not read as a car. The two faults
      are almost always fixed in one edit - when the builder is told about
      both.

    ``look`` is still accepted so that a call written against the old schema
    is not a TypeError, but it decides nothing. ``grid_only`` is the one
    remaining way to skip, and it is not in the tool schema: it exists for the
    offline self-tests, which must not reach for a renderer or a vision model.
    """
    del look  # accepted for compatibility; the looking is not optional
    target = _resolve(path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    # What this run was told it is building - one object, or a scene of them.
    # It rides on the ledger rather than being a parameter of the tool: whether
    # this file is one object is the harness's knowledge, not a judgement the
    # builder should be able to talk itself out of. See RunState.objects.
    objects = getattr(state, "objects", None) if state is not None else None
    report = validation.validate(target, tolerance=float(tolerance or 2.0),
                                 objects=objects)

    # The lattice first, because it is upstream of everything else. A model
    # built across two grids half a stud apart reports one misalignment per
    # part on the losing side - dozens of them - and every one is the same
    # ±10 LDU correction. Repairing that here turns a report the builder would
    # have spent its whole remaining budget on into a note saying it was done.
    # Held in a local rather than written straight onto the report: the overlap
    # pass below re-validates, which replaces `report` wholesale, and a note
    # attached before that happens is a note the builder never sees.
    lattice_fixed = None
    if (report.get("lattice") or {}).get("on_two_lattices"):
        snapped = autofix.snap_lattice(target)
        if snapped.get("changed"):
            lattice_fixed = snapped
            report = validation.validate(target,
                                         tolerance=float(tolerance or 2.0),
                                         objects=objects)
        elif snapped.get("note"):
            report.setdefault("lattice", {})["not_repaired"] = snapped["note"]

    # The overlaps that are pure arithmetic get repaired here rather than
    # reported: a brick one stud too far east is not something worth spending a
    # turn of the model's attention on, and the fix is unambiguous. Only what
    # survives that is put in front of it - which is the set of overlaps that
    # genuinely need a decision. Nothing is written unless something moved, and
    # nothing that moves changes a line number, so a plan made against this
    # file's numbering still holds.
    if (report.get("collision") or {}).get("overlapping"):
        # One pass repairs a limited number of overlaps, so it is run until it
        # stops making progress rather than once. It used to stop after one and
        # tell the model to call `fix_collision` itself for the rest, which put
        # a whole tool in the schema to do what a while loop does - and cost a
        # turn of the model's attention on arithmetic it was never needed for.
        moved, fixed = 0, {}
        for _ in range(AUTOFIX_PASSES):
            fixed = autofix.fix(target)
            if "error" in fixed or not fixed.get("changed"):
                break
            moved += fixed.get("moved", 0)
            report = validation.validate(target, tolerance=float(tolerance or 2.0),
                                         objects=objects)
            if not (report.get("collision") or {}).get("overlapping"):
                break

        if "error" not in fixed and (moved or fixed.get("remaining")):
            report["auto_fixed"] = {
                "moved": moved,
                # what is still wrong after every pass, which is now the whole
                # of what is left rather than what one pass happened to reach
                "remaining": fixed.get("remaining", 0),
                "moved_parts": fixed.get("fixed_parts", []),
                "unfixed_parts": fixed.get("unfixed_parts", []),
                "note": _autofix_note({**fixed, "moved": moved,
                                       "not_reached": 0}),
            }

    # The model's size, which used to be a tool of its own. It is one walk of
    # the same file this call has already read, and every caller that wanted it
    # was about to validate anyway.
    if lattice_fixed is not None:
        report["lattice_fixed"] = lattice_fixed

    size = _measure_model(path)
    if "error" not in size:
        report["size"] = {k: size[k] for k in
                          ("size_ldu", "size_studs", "min", "max",
                           "ground_y", "top_y") if k in size}

    # The ceiling on how many pieces a build may end in - said here, where
    # there is still time to do something about it, rather than only by the
    # finish gate. A model can pass every fault check on the page and still be
    # refused for this, so a builder that only met it at `finish` would meet it
    # believing it was done. See runstate.MAX_SUBASSEMBLIES.
    if runstate.too_many_pieces(report, objects):
        pieces = (report.get("connectivity") or {}).get("subassemblies")
        report["too_many_subassemblies"] = {
            "subassemblies": pieces,
            "allowed": runstate.MAX_SUBASSEMBLIES,
            "note": (f"This is ONE object and the studs read it as {pieces} "
                     f"separate pieces. A build may finish with at most "
                     f"{runstate.MAX_SUBASSEMBLIES}, so `finish` will be "
                     f"refused until this comes down - whatever the verdict "
                     f"above says. `loose_pieces` names the clumps that are "
                     f"not the main body; seat each one on real studs of the "
                     f"build, or bridge it with a plate that reaches both."),
        }

    if state is not None:
        state.record_validation(path, report)

    # ...and then look at it, which is the other half of knowing whether a
    # model is finished. Unconditionally: see the docstring for why neither
    # the builder nor a failing grid check gets to skip this any more.
    if grid_only:
        # Not reachable from the tool schema. The offline self-tests come
        # through here, and they have neither a renderer nor a vision model.
        report["seen_note"] = ("not looked at: grid_only, which is for the "
                               "offline tests and is not something the "
                               "builder can ask for.")
        return report

    # A renderer that is missing must cost the run its eyes, not its
    # validation - the grid check above is true either way.
    try:
        seen = _render_model(path, question=question, state=state)
    except Exception as exc:
        seen = {"error": f"{type(exc).__name__}: {exc}"}
    if "error" in seen:
        report["seen_note"] = f"could not look at it: {seen['error']}"
    else:
        report.update(seen)
        if not report.get("passed"):
            # Said out loud, because the two reports now arrive together and
            # they are about different things. Without this the builder reads
            # a critique of a model it has already been told is broken and
            # treats the critique as the thing to fix.
            report["seen_note"] = (
                "The grid check above failed and this is what the model looks "
                "like anyway. Both are real and both are yours: fix the faults "
                "and apply what the render says in the same edit, rather than "
                "repairing the geometry now and finding out next iteration "
                "that the shape was wrong too.")

    return report


# Tools that take a `should_stop` predicate: the ones long enough that waiting
# for the agent loop's next check would leave Stop looking broken.
STOPPABLE = frozenset(("plan_construction",))

# Tools that read or write the run's ledger - what has been built, checked and
# looked at. Everything else is a lookup and has no business knowing.
STATEFUL = frozenset((
    "edit_model", "build_ops", "copy_from_set", "validate_model",
    # For the design brief only, which the ledger carries so that the plan is
    # written against it without the builder having to pass it along.
    "plan_construction",
    "assemble_model", "move_submodel", "rotate_submodel",
    "finish", "ask_about_image",
    # The two that find parts. They need the ledger only for the project name,
    # which is what the palette of already-found parts is filed under.
    "search_parts", "get_part_details",
))

DISPATCH = {
    "plan_construction": _plan_construction,
    # finding things
    "search_parts": _search_parts,
    "search_reference": _search_reference,
    "get_part_details": _get_part_details,
    "get_set_details": _get_set_details,
    "read_model": _read_source,
    # making things
    "build_ops": _build_ops,
    "copy_from_set": _copy_from_set,
    "edit_model": _edit_model,
    "validate_model": _validate_model,
    # the reference picture
    "ask_about_image": _ask_about_image,
    # keeping things
    # the harness's own, and the end
    "assemble_model": _assemble_model,
    "move_submodel": _move_submodel,
    "rotate_submodel": _rotate_submodel,
    "finish": _finish,
}


def call_tool(name, arguments, should_stop=None, state=None):
    """Run a tool by name. Returns a JSON string for the model.

    ``should_stop`` is handed only to the tools that can act on it - the ones
    that spend real time inside a single call. Everywhere else a stop is
    caught by the agent loop between calls, which is soon enough.

    ``state`` is the run's ledger (see runstate.RunState), handed only to the
    tools in STATEFUL. It is what makes ``finish`` able to refuse.
    """
    fn = DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        arguments = dict(arguments or {})
        if should_stop is not None and name in STOPPABLE:
            arguments["should_stop"] = should_stop
        if state is not None and name in STATEFUL:
            arguments["state"] = state
        result = fn(**arguments)
    except TypeError as e:
        result = {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # surfaced to the model so it can recover
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result, ensure_ascii=False, default=str)
