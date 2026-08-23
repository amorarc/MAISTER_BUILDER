#!/usr/bin/env python3
"""
CLI for the LDraw model builder agent.

    conda activate hf_env
    export HF_TOKEN=hf_...

    python -m maister.agent.run_agent "Build a 4x4 house with a red roof"
    python -m maister.agent.run_agent --task-file task.txt --model deepseek-ai/DeepSeek-V4-Flash:fastest

Offline checks (no HF_TOKEN needed):

    python -m maister.agent.run_agent --self-test
    python -m maister.agent.run_agent --validate out/my_model/house.mpd
    python -m maister.agent.run_agent --show-prompt
"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "maister.agent"

from .agent import LDrawAgent                     # noqa: E402
from .config import DEFAULT_MAX_STEPS, DEFAULT_MODEL, DEFAULT_TEMPERATURE  # noqa: E402
from .llm import ENV_FILE, LLM, MissingToken, make_client, resolve_token  # noqa: E402
from .orchestrator import Orchestrator            # noqa: E402
from .prompts import build_system_prompt          # noqa: E402
from .runstate import RunState                    # noqa: E402
from .tools import call_tool                      # noqa: E402


def _create(path, content):
    """Start a model file the way the agent does: insert it all before line 1.

    There is no write_model any more — a file that does not exist is an empty
    file, and `edit_model` fills it. Using it here rather than reaching past it
    is deliberate: the self-test exercises the create path every time it needs
    a model on disk.
    """
    Path(_resolve_out(path)).unlink(missing_ok=True)
    return json.loads(call_tool("edit_model", {
        "path": path,
        "edits": [{"op": "insert", "start_line": 1, "lines": content}]}))


def _resolve_out(path):
    from .config import OUT_DIR
    return OUT_DIR / path


def _check_undefined():
    """Every module in the project read for names that are used but not bound.

    A NameError is invisible to ``compileall`` and invisible to any test that
    does not happen to run the exact line. Reading the source finds them all at
    once, which is the only way this class of bug stops recurring.

    Raises AssertionError on the first one found. Skipped, with a note, when
    pyflakes is not installed — this is a check, not a dependency.
    """
    try:
        from pyflakes import api, reporter
    except ImportError:
        return ("SKIPPED - pyflakes is not installed "
                "(pip install pyflakes) so undefined names are unchecked")

    import io
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out, err = io.StringIO(), io.StringIO()
    targets = sorted(
        str(p) for folder in ("maister", "app/backend")
        for p in (root / folder).rglob("*.py")
        if "__pycache__" not in p.parts
    )
    api.checkRecursive(targets, reporter.Reporter(out, err))

    # Only undefined names matter here. Unused imports and the like are style,
    # and failing a self-test over style would make it something people skip.
    bad = [line for line in out.getvalue().splitlines()
           if "undefined name" in line]
    assert not bad, ("names used but never defined:\n  " + "\n  ".join(bad))
    return f"{len(targets)} modules read, no undefined names"


def _preflight():
    """What must be on disk before any of this means anything.

    Without it a fresh clone fails deep inside validation with `unresolved_parts`
    and an assertion about a stack that is in fact perfectly correct — the parts
    simply have no geometry to check against. That is a true report and a
    useless first experience, so the missing data is named here instead, with
    the command that fetches it.
    """
    from .config import PARTS_CATALOG, PARTS_DIR, VECTOR_DB_DIR

    missing = []
    if not PARTS_DIR.is_dir() or not any(PARTS_DIR.iterdir()):
        missing.append(
            f"  the LDraw parts library   {PARTS_DIR}\n"
            f"      Nothing can be measured, rendered or validated without it.")
    if not PARTS_CATALOG.is_file():
        missing.append(
            f"  the part catalogue        {PARTS_CATALOG}\n"
            f"      Ships with the repo; if it is gone, rebuild it.")
    if not VECTOR_DB_DIR.is_dir() or not any(VECTOR_DB_DIR.glob("*")):
        missing.append(
            f"  the search indexes        {VECTOR_DB_DIR}\n"
            f"      Only semantic search needs these; the rest works without.")

    if missing:
        print("This checkout is missing data it needs:\n")
        print("\n".join(missing))
        print("\nFetch and build all of it with:\n\n    ./scripts/fetch_data.sh\n")
        return False
    return True


def self_test():
    """Exercise every tool without touching the network."""
    if not _preflight():
        return 2
    print("1. search_parts('brick 2 x 4')")
    r = json.loads(call_tool("search_parts", {"query": "brick 2 x 4"}))
    for row in r.get("results", [])[:3]:
        print(f"   {row['part_id']:10s} {row['description']}")
    assert r.get("results"), "catalogue search returned nothing"

    print("2. get_part_details('3003')")
    r = json.loads(call_tool("get_part_details", {"part_id": "3003"}))
    print(f"   {r.get('description')}  studs={r.get('stud_grid')}  "
          f"place_height={r.get('place_height_ldu')}")
    assert r.get("stud_grid"), "no stud grid derived"

    print("3. edit_model creates + validate_model (a deliberately correct 3-brick stack)")
    content = "\n".join([
        "0 FILE selftest.ldr",
        "0 Self Test Stack",
        "0 Name: selftest.ldr",
        "0 Author: LDraw Model Builder Agent",
        "0 !LDRAW_ORG Model",
        "0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt",
        "",
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3003.dat",
        "1 14 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat",
        "1 15 0 -48 0 1 0 0 0 1 0 0 0 1 3003.dat",
        "0 STEP",
    ])
    print("  ", _create("agent_selftest/selftest.ldr", content))
    v = json.loads(call_tool("validate_model",
                             {"path": "agent_selftest/selftest.ldr"}))
    print(f"   verdict: {v.get('verdict')}")
    print(f"   connectivity: {v.get('connectivity', {}).get('connected')} connected, "
          f"{v.get('connectivity', {}).get('misaligned')} misaligned")
    assert v.get("passed"), f"a valid stack failed validation: {v}"

    print("4. validate_model on a deliberately BROKEN stack (off-grid plate)")
    broken = "\n".join([
        "0 FILE broken.ldr",
        "0 Broken",
        "0 Name: broken.ldr",
        "0 Author: LDraw Model Builder Agent",
        "0 !LDRAW_ORG Model",
        "0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt",
        "",
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3003.dat",
        "1 14 0 -8 14 1 0 0 0 1 0 0 0 1 3024.dat",
    ])
    _create("agent_selftest/broken.ldr", broken)
    v = json.loads(call_tool("validate_model", {"path": "agent_selftest/broken.ldr"}))
    mis = v.get("connectivity", {}).get("misaligned_parts", [])
    print(f"   verdict: {v.get('verdict')}")
    for m in mis:
        print(f"   caught: line {m['line']} {m['part']} gap={m['gap_ldu']} LDU")
    assert not v.get("passed") and mis, "the broken model was not caught"

    print("4b. both directions at once: correct builds pass, broken ones fail")
    _buildable_check()

    print("4c. decoration faces a direction, and every facing is placeable")
    _facing_check()

    print("4d. parts held up by nothing are caught; standing ones are not")
    _floating_check()

    print("4e. one object must be one piece; separate objects are left apart")
    _connected_check()

    print("4e2. and it may not end as a heap of clumps the studs never joined")
    _subassembly_gate_check()

    print("4c2. a part on a side stud connects however the host is turned")
    _side_stud_check()

    print("4e3. the acceptance checklist: what it refuses to let finish")
    _requirements_check()

    print("4e4. context: what reaches the builder and the critic")
    _context_check()

    print("4e5. every part the prompt hands out without a lookup is real")
    _cited_parts_check()

    print("4f. the design brief: the mode for a plain request, the tail for an "
          "invited one")
    _brief_sampling_check()

    print("4g. the visual critic is grounded in what was measured")
    _critic_grounding_check()

    print("5. edit_model: line edits, the expect guard, all-or-nothing")
    # The stack from step 3 is on disk: 6 header lines, a blank, three parts,
    # then "0 STEP".
    r = json.loads(call_tool("edit_model", {
        "path": "agent_selftest/selftest.ldr",
        "edits": [
            # recolour the middle brick, delete the top one, add a fourth
            {"op": "replace", "start_line": 9,
             "expect": "1 14 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat",
             "lines": "1 2 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat"},
            {"op": "delete", "start_line": 10,
             "expect": "1 15 0 -48 0 1 0 0 0 1 0 0 0 1 3003.dat"},
            {"op": "insert", "start_line": 11,
             "lines": "1 1 0 -48 0 1 0 0 0 1 0 0 0 1 3003.dat"},
        ]}))
    print(f"   3 edits -> {r.get('lines')} lines "
          f"(was {r.get('was_lines')}), {len(r.get('applied') or [])} applied")
    assert "error" not in r, f"a good edit was refused: {r}"
    after = json.loads(call_tool("read_model",
                                 {"source": "agent_selftest/selftest.ldr"}))["content"]
    assert "1 2 0 -24 0" in after, f"the replace did not land: {after}"
    assert "1 15 0 -48 0" not in after, f"the delete did not land: {after}"
    assert "1 1 0 -48 0" in after, f"the insert did not land: {after}"
    assert r["lines"] == r["was_lines"], \
        f"one line out, one line in, and the total moved: {r}"
    v = json.loads(call_tool("validate_model",
                             {"path": "agent_selftest/selftest.ldr"}))
    print(f"   the edited stack: {v.get('verdict')}")
    assert v.get("passed"), f"editing broke a model that was valid: {v}"

    # A stale line number must never quietly change the wrong part.
    before_guard = after
    r = json.loads(call_tool("edit_model", {
        "path": "agent_selftest/selftest.ldr",
        "edits": [{"op": "delete", "start_line": 9,
                   "expect": "1 15 0 -48 0 1 0 0 0 1 0 0 0 1 3003.dat"}]}))
    print(f"   a stale `expect` -> refused: {'error' in r}")
    assert "error" in r and r.get("actual"), \
        f"an edit against the wrong line was accepted: {r}"

    # And a batch with one bad edit in it changes nothing at all.
    r = json.loads(call_tool("edit_model", {
        "path": "agent_selftest/selftest.ldr",
        "edits": [{"op": "replace", "start_line": 8, "expect": "",
                   "lines": "0 // fine"},
                  {"op": "delete", "start_line": 9999, "expect": "whatever"}]}))
    unchanged = json.loads(call_tool(
        "read_model", {"source": "agent_selftest/selftest.ldr"}))["content"]
    print(f"   a batch with one bad edit -> refused, file untouched: "
          f"{unchanged == before_guard}")
    assert "error" in r and unchanged == before_guard, \
        f"a partly-bad batch was partly applied: {r}"

    # Missing `expect` on a destructive edit is refused outright.
    r = json.loads(call_tool("edit_model", {
        "path": "agent_selftest/selftest.ldr",
        "edits": [{"op": "delete", "start_line": 9}]}))
    print(f"   delete without `expect` -> refused: {'error' in r}")
    assert "error" in r, "a delete with no guard was allowed"

    print("6. the ask_about_image budget (no vision call needed)")
    from .runstate import RunState as _RunState

    budget = _RunState(subject="a stack", project="agent_selftest")
    print(f"   a fresh run may ask: {budget.may_ask()} "
          f"({budget.asks_left()} left)")
    assert budget.may_ask(), "a run started with no questions to spend"
    budget.record_ask(["how many windows?"], {"answers": []})
    print(f"   after one ask: {budget.may_ask()} ({budget.asks_left()} left)")
    assert budget.may_ask(), "one question exhausted the whole allowance"

    for i in range(budget.max_asks - 1):
        budget.record_ask([f"and now? {i}"], {"answers": []})
    print(f"   after {budget.max_asks}: {budget.may_ask()} "
          f"({budget.asks_left()} left)")
    assert not budget.may_ask(), "the allowance was never exhausted"

    # A write does not buy more: the cap is for the whole build.
    budget.record_write("agent_selftest/selftest.ldr")
    print(f"   a write does not restore it: {budget.may_ask()}")
    assert not budget.may_ask(), "a write reopened a spent allowance"
    assert budget.asked_questions()[0] == "how many windows?", \
        f"the run did not remember what it asked: {budget.asked_questions()[:2]}"

    # With no reference image attached, asking is refused without spending
    # anything — there is nothing to look at.
    r = json.loads(call_tool("ask_about_image", {"questions": ["what colour?"]},
                             state=_RunState(project="agent_selftest")))
    print(f"   no reference image -> refused: {'error' in r}")
    assert "error" in r, "a question was put to a picture that does not exist"

    print("7. the harness: geometry, assembly, the finish gate")
    from . import render
    from .config import OUT_DIR
    from .runstate import RunState

    state = RunState(subject="a stack of 2x2 bricks", project="agent_selftest",
                     require_render=False)
    Path(_resolve_out("agent_selftest/selftest.ldr")).unlink(missing_ok=True)
    call_tool("edit_model", {"path": "agent_selftest/selftest.ldr",
                             "edits": [{"op": "insert", "start_line": 1,
                                        "lines": content}]}, state=state)
    report = json.loads(call_tool("validate_model",
                                  {"path": "agent_selftest/selftest.ldr"}))
    size = report.get("size", {}).get("size_studs", {})
    print(f"   validate_model size -> {size['width']}x{size['depth']} studs, "
          f"{size['height_bricks']} bricks tall")
    assert size["width"] == 2.0, f"wrong footprint: {report.get('size')}"

    r = json.loads(call_tool("finish", {"summary": "done"}, state=state))
    print(f"   finish before validating -> refused: {not r['finished']}")
    assert not r["finished"] and r["problems"], \
        f"the gate let an unvalidated run finish: {r}"

    call_tool("validate_model", {"path": "agent_selftest/selftest.ldr"},
              state=state)
    r = json.loads(call_tool("finish", {"summary": "A three-brick stack; "
                                                   "validation passed."},
                             state=state))
    print(f"   finish after validating -> accepted: {r['finished']}")
    assert r["finished"], f"the gate refused a finished run: {r}"

    r = json.loads(call_tool("finish", {"summary": "stuck", "give_up": True},
                             state=state))
    assert not r["finished"], "give_up was accepted with no reason given"
    print(f"   give_up with no reason -> refused: {not r['finished']}")

    # two subbuilds into one scene, placed without overlapping
    _create("agent_selftest/a.ldr",
                              content.replace("selftest.ldr", "a.ldr"))
    _create("agent_selftest/b.ldr",
                              content.replace("selftest.ldr", "b.ldr"))
    r = json.loads(call_tool("assemble_model", {
        "path": "agent_selftest/scene.mpd", "title": "Self Test Scene",
        "components": [{"file": "agent_selftest/a.ldr", "name": "a"},
                       {"file": "agent_selftest/b.ldr", "name": "b"}]}))
    places = [c["at"][0] for c in r["components"]]
    print(f"   assemble_model -> 2 components at x = {places}")
    v = json.loads(call_tool("validate_model", {"path": "agent_selftest/scene.mpd"}))
    print(f"   the assembled scene: {v['verdict']}")
    assert v["passed"], f"a scene assembled from two valid models failed: {v}"

    print(f"   LeoCAD available: {render.available()}")
    if render.available():
        scene = OUT_DIR / "agent_selftest" / "scene.mpd"
        images = render.render_model_file(scene, project="agent_selftest")
        print(f"   rendered {len(images)} view(s): "
              f"{', '.join(name for name, _ in images)}")
        assert len(images) >= 3, "most viewpoints failed to render"
    else:
        print("   SKIPPED rendering - LeoCAD is not on PATH")

    print("8. the orchestrator's offline paths")
    # Everything here runs without the network. It exists because a NameError
    # in the orchestrator's main path once survived every check in this file:
    # nothing imported it, so nothing resolved the names it uses at runtime.
    from . import planner
    from .decompose import Subconstruction, fold_attached
    from .orchestrator import ASSEMBLY_ONLY, SUBBUILD_EXCLUDED, Orchestrator
    from .orchestrator import _tools as _orchestrator_tools

    house = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat"
    print(f"   is_modification('add a chimney', <a model>) -> "
          f"{planner.is_modification('add a chimney', house)}")
    assert planner.is_modification("add a chimney", house), \
        "an edit to an existing model was not recognised as one"
    assert not planner.is_modification("add a chimney", ""), \
        "an edit was claimed against a model that does not exist"
    assert not planner.is_modification("what colour is it?", house), \
        "a question was treated as an edit"

    kept, folded = fold_attached([
        Subconstruction("house", "a house"),
        Subconstruction("grass", "a patch of grass", extends="house"),
    ])
    print(f"   fold_attached -> kept {[k.name for k in kept]}, folded {folded}")
    assert [k.name for k in kept] == ["house"] and folded, \
        "detail was not folded into the object it belongs to"

    counts = (len(_orchestrator_tools(exclude=SUBBUILD_EXCLUDED)),
              len(_orchestrator_tools(only=set(ASSEMBLY_ONLY))))
    print(f"   tool sets -> subbuild {counts[0]}, assembly {counts[1]}")
    assert counts[0] > counts[1] > 0, "the narrowed tool sets look wrong"

    orchestrator = Orchestrator(llm=None, verbose=False)
    orchestrator.subconstructions = [Subconstruction("car", "a red car")]
    # On a file that EXISTS, so it runs past the early return and actually
    # reaches `planner.has_parts`. Pointed at a missing file this proved
    # nothing, which is how the NameError survived the first version of this
    # very check.
    assert orchestrator._read("nope/nothing.ldr") is None, \
        "_read invented a file that is not there"
    loaded = orchestrator._read("agent_selftest/selftest.ldr")
    print(f"   _read(existing) -> {len((loaded or '').splitlines())} lines")
    assert loaded, "_read returned nothing for a model that has parts in it"

    task = orchestrator._subbuild_task(
        orchestrator.subconstructions[0], 1, 1, "build a red car", house,
        modifying=True)
    assert "Change the existing model" in task, \
        f"an edit did not produce an edit brief: {task[:200]}"
    print("   _subbuild_task(modifying=True) -> produced an edit brief")

    # The assembly path end to end, with no model in the loop.
    for name in ("aa", "bb"):
        _create(f"agent_selftest/{name}.ldr",
                content.replace("selftest.ldr", f"{name}.ldr"))
    built = [Subconstruction(n, f"a {n}") for n in ("aa", "bb")]
    for sub in built:
        sub.path = f"agent_selftest/{sub.name}.ldr"
    orchestrator._assemble(built, "agent_selftest/orch_scene.mpd",
                           {"summary": "Self Test Scene"}, "agent_selftest")
    v = json.loads(call_tool("validate_model",
                             {"path": "agent_selftest/orch_scene.mpd"}))
    print(f"   _assemble -> {v.get('verdict')}")
    assert v.get("passed"), f"the orchestrator assembled an invalid scene: {v}"

    print("9. undefined names across the package")
    # The general form of the bug above: a name used but never imported. No
    # amount of exercising individual functions catches these reliably — only
    # the lines that happen to run get checked, and a NameError on a branch
    # nothing here reaches still ships. This reads every module instead.
    print("  ", _check_undefined())

    print("10. semantic search (needs the vector databases)")
    from ..retrieval import status
    info = status()
    if not (info.get("parts") or {}).get("available"):
        print("   SKIPPED - no vector databases. Build them with:")
        print("     python -m maister.retrieval.build_indexes")
    else:
        print(f"   {info['embedding_model']} on {info['device']}; "
              f"{info['parts']['count']} parts, "
              f"{(info.get('sets') or {}).get('count', 0)} sets indexed")

        # a query with no term in common with the answer's description: this
        # only works if the embeddings are real
        r = json.loads(call_tool("search_parts",
                                 {"query": "something curved for a car roof"}))
        top = r.get("results", [])
        print(f"   search_parts (by description) -> {top[0]['part_id']} "
              f"{top[0]['description'].strip()}")
        assert top, "semantic part search returned nothing"
        assert "curv" in top[0]["description"].lower(), \
            f"semantic search missed the obvious answer: {top[0]}"

        r = json.loads(call_tool("search_reference",
                                 {"kind": "sets", "query": "x-wing starfighter",
                                  "max_results": 3}))
        hits = r.get("results", [])
        for row in hits:
            print(f"   search_reference(sets) -> {row['set_number']:9s} {row['set_name']}")
        assert hits, "set search returned nothing"

        print("11. set reference tools")
        found = hits[0]["set_number"]
        d = json.loads(call_tool("get_set_details", {"set_number": found}))
        blocks = d.get("submodels") or []
        print(f"   get_set_details({found}) -> {d.get('total_lines')} lines, "
              f"{len(blocks)} submodel(s), source "
              f"{'present' if d.get('model_source') else 'MISSING'}")
        assert "0 FILE" in (d.get("model_source") or ""), \
            "get_set_details returned no LDraw source"
        assert not any(k in d for k in ("most_used_parts", "theme", "year")), \
            "set metadata is back in what should be source only"

        d = json.loads(call_tool("read_model", {"source": f"set:{found}",
                                          "end_line": 6}))
        print(f"   read_model(set:{found}) -> {d.get('total_lines')} lines of LDraw")
        assert "0 FILE" in (d.get("content") or ""), "reference model looks empty"

        if blocks:
            wanted = blocks[-1]["name"]
            d = json.loads(call_tool("read_model", {"source": f"set:{found}",
                                                    "submodel": wanted}))
            shown = d.get("shown_lines") or [0, 0]
            print(f"   read_model(submodel={wanted!r}) -> lines "
                  f"{shown[0]}-{shown[1]}, {d.get('submodel')}")
            assert d.get("submodel") == wanted and "0 FILE" in (d.get("content") or ""), \
                f"reading one submodel of a set did not work: {d.get('error')}"
            miss = json.loads(call_tool("read_model", {
                "source": f"set:{found}", "submodel": "no such block at all"}))
            print(f"   an unknown submodel -> refused, names listed: "
                  f"{'submodels' in miss}")
            assert "error" in miss and miss.get("submodels"), \
                "a bad submodel name did not come back with the real ones"

        d = json.loads(call_tool("search_reference", {"kind": "sets", "like": found,
                                                      "max_results": 3}))
        near = d.get("results", [])
        print("   search_reference(like=) -> " + ", ".join(
            f"{r['set_number']} ({r['similarity']:.2f})" for r in near))
        assert near, "similarity search returned nothing"

        print("12. the agent's own library and notes")
        # Everything here is written under a reserved name and removed again, so
        # a self-test never leaves junk in the real library.
        probe = "__selftest probe model"
        _create("agent_selftest/probe.ldr",
                                  content.replace("selftest.ldr",
                                                             "probe.ldr"))
        # Saving is a button now, not a tool — this is what the button calls.
        from .tools import _save_creation

        r = _save_creation("agent_selftest/probe.ldr", probe,
                           "A three-brick stack of 2x2 bricks, used to test the "
                           "creation library. Not a real model.",
                           tags=["selftest"], require_valid=True)
        saved = r.get("saved", {})
        print(f"   save to gallery -> {saved.get('total_pieces')} pieces, "
              f"validated={saved.get('validated')}, indexed={r.get('indexed')}")
        assert saved.get("validated"), f"a valid model saved as invalid: {r}"

        # and it refuses a model that does not sit on the grid
        _create("agent_selftest/badsave.ldr",
                                  broken)
        r = _save_creation("agent_selftest/badsave.ldr", "__selftest broken",
                           "should never be saved", require_valid=True)
        print(f"   save refuses an invalid model: {'error' in r}")
        assert "error" in r, f"a broken model was allowed into the gallery: {r}"

        r = json.loads(call_tool("search_reference",
                                 {"kind": "creations",
                                  "query": "a stack of 2x2 bricks"}))
        hits = [c["name"] for c in r.get("results", [])]
        print(f"   search_reference(creations) -> {hits}")
        assert probe in hits, "a just-saved creation was not findable"

        # the floor must hold: nothing in the library is a castle
        r = json.loads(call_tool("search_reference",
                                 {"kind": "creations",
                                  "query": "a large medieval castle with towers"}))
        print(f"   search_reference(creations, irrelevant) -> "
              f"{len(r.get('results', []))} results, as intended")
        assert not r.get("results"), \
            f"the relevance floor let an unrelated creation through: {r}"

        # Notes are no longer something the agent files — there is no tool for
        # it any more. The store is still here and still read: a note reaches a
        # build through `get_part_details`, which is the half of this that
        # matters and the half tested below.
        from ..agent import notes as _notes_store
        from ..retrieval import search as _search_index

        record, error = _notes_store.add(
            "part", "3003",
            "__selftest probe note: 2x2 bricks stack cleanly at 24 LDU.")
        note_id = (record or {}).get("note_id")
        indexed = bool(record) and bool(_search_index.index_note(record))
        print(f"   notes.add -> {(record or {}).get('subject_id')}, "
              f"indexed={indexed}")
        assert note_id, f"note was not saved: {error}"

        _, error = _notes_store.add("part", "definitely-not-a-part",
                                    "should be rejected")
        print(f"   a note against a nonexistent part -> rejected: {bool(error)}")
        assert error, "a note was filed against a part that does not exist"

        attached = _search_index.notes_for("part", "3003")
        print(f"   notes_for(part:3003) -> {len(attached)} note(s)")
        assert attached, "an exact subject lookup found nothing"

        d = json.loads(call_tool("get_part_details", {"part_id": "3003"}))
        print(f"   get_part_details(3003) surfaces notes: "
              f"{bool(d.get('your_notes'))}")
        assert d.get("your_notes"), "notes did not surface on the part they describe"

        from ..agent import creations as _creations
        from ..agent import notes as _notes
        from ..retrieval import search as _search
        _notes.delete(note_id)
        _search.unindex_note(note_id)
        record = _creations.delete(probe)
        if record:
            _search.unindex_creation(record["creation_id"])
        print("   cleaned up the probe creation and note")

    print("13. minifigures (assembled, not built on studs)")
    _minifig_check()

    print("14. instruction pages: the split that renders them in parallel")
    _pagination_check()

    print("\nSelf-test passed: catalogue, geometry, retrieval, memory, writing "
          "and validation all work.")
    return 0


def _buildable_check():
    """Correct building must pass, and every real fault must still fail.

    Half of this is not obvious and is the half that rots. A checker is easy to
    make strict and easy to make quiet, and only the two together are worth
    anything — so the cases below are paired on purpose: a part with a clip and
    a Technic pin, which the stud grid does not govern and which must pass;
    and a duplicate, a half-stud slip and a buried brick, which it does and
    which must fail.

    Calibrated against the 1,801 official sets in data/ldraw_omr_sets, which
    are the only ground truth there is for "buildable": they were designed,
    moulded and sold. Every "must pass" case here is a shape those sets are
    full of and this checker used to reject.

    A part's origin sits on its TOP face and +Y is down, so a brick occupies
    y 0..+24 *below* its origin and anything resting on it has its own origin
    at minus its own height. Getting that wrong is the easiest way to write a
    "correct" case that is genuinely broken.
    """
    head = ("0 FILE t.ldr\n0 Test\n0 Name: t.ldr\n"
            "0 Author: LDraw Model Builder Agent\n0 !LDRAW_ORG Model\n")
    cases = [
        # (name, body, must_pass)
        ("a plate on a brick", True,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 1 0 -8 0 1 0 0 0 1 0 0 0 1 3020.dat\n"),
        # 3020 is 80 x 40, so its studs are at x = +-10, +-30 and z = +-10.
        ("tiles on a plate", True,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3020.dat\n"
         "1 1 -20 -8 -10 1 0 0 0 1 0 0 0 1 3069b.dat\n"
         "1 1 20 -8 -10 1 0 0 0 1 0 0 0 1 3069b.dat\n"),
        # 60897 is a 1x1 plate with a clip: its box measures 20 x 34, and the
        # 14 LDU the clip adds used to invent two seats either side of the one
        # place it can sit.
        ("a part with a clip, seated", True,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3020.dat\n"
         "1 1 -10 -8 -10 1 0 0 0 1 0 0 0 1 60897.dat\n"),
        # A pin in a pin hole is not on a stud and never was.
        ("a Technic pin in a beam", True,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3701.dat\n"
         "1 0 0 -10 0 1 0 0 0 1 0 0 0 1 3673.dat\n"),
        ("a brick one stud inside another", False,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 14 20 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        ("the same brick placed twice", False,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        # A 6x6 plate's studs are at +-10, +-30, +-50 — never at 0.
        ("a 1x1 plate between four studs", False,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3958.dat\n"
         "1 14 0 -8 0 1 0 0 0 1 0 0 0 1 3024.dat\n"),
        # The two that guard `classify`'s one deliberate blind spot. A part
        # that something mates with is no longer called misaligned for
        # near-missing a different stud — see the note there — and the reason
        # that is safe is that a misplaced part still breaks the seating of
        # whatever it failed to sit on, so the model fails anyway. These pin
        # that. If either ever passes, the relaxation has stopped being safe
        # and this is where it says so.
        ("a half-off brick with another stacked on it", False,
         "1 4 0   0   0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 0 -24  10 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 1 0 -48  10 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        # Slopes, wedges and brackets used to be exempt from overlap checking
        # wholesale, so these three were invisible. They are the cases that
        # motivated occupancy.py and they are pinned here.
        ("two 2x2 slopes one stud apart", False,
         "1 4  0 0 0 1 0 0 0 1 0 0 0 1 3039.dat\n"
         "1 2 20 0 0 1 0 0 0 1 0 0 0 1 3039.dat\n"),
        ("a 2x4 brick buried in a 2x4 double slope", False,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 0 0 0 1 0 0 0 1 0 0 0 1 3041.dat\n"),
        ("two 4x4 dishes in the same place", False,
         "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3960.dat\n"
         "1 2 0 0 0 1 0 0 0 1 0 0 0 1 3960.dat\n"),
        # ...and the other direction, which is what the exemptions were for.
        # Every one of these was a false positive the old check had to be told
        # about by name; the geometry answers them without being told.
        ("two 2x2 slopes correctly side by side", True,
         "1 4  0 0 0 1 0 0 0 1 0 0 0 1 3039.dat\n"
         "1 2 40 0 0 1 0 0 0 1 0 0 0 1 3039.dat\n"),
        ("a plate carried on a bracket's upstand", True,
         "1 4 0  0   0 1 0 0 0 1 0 0 0 1 99207.dat\n"
         "1 2 0 -8 -20 1 0 0 0 1 0 0 0 1 3024.dat\n"),
        # A slope against a *small* part, which is where an absolute volume
        # threshold stopped being sensitive enough: both of these are a whole
        # stud of solid plastic and both validated clean until
        # collisions.SHARED_FRACTION existed. The pair below them is the same
        # slope with the brick correctly on top of it, so a rule that catches
        # these by simply distrusting slopes does not pass.
        ("a 1x1 brick inside a 2x2 slope", False,
         "1 4 0 0   0 1 0 0 0 1 0 0 0 1 3039.dat\n"
         "1 2 0 0 -10 1 0 0 0 1 0 0 0 1 3005.dat\n"),
        ("a 1x1 brick half a stud into a 2x2 slope", False,
         "1 4  0 0   0 1 0 0 0 1 0 0 0 1 3039.dat\n"
         "1 2 20 0 -10 1 0 0 0 1 0 0 0 1 3005.dat\n"),
        # On a real stud of it: 3039's top studs are at x +-10, z -20 and 0.
        ("a 1x1 brick correctly on top of a 2x2 slope", True,
         "1 4   0   0   0 1 0 0 0 1 0 0 0 1 3039.dat\n"
         "1 2 -10 -24 -20 1 0 0 0 1 0 0 0 1 3005.dat\n"),
        # A full stud of interpenetration between two parts small enough that
        # the shared plastic still comes in under an allowance sized for two
        # 2x4 bricks. Every one of these validated clean until
        # collisions.SHARED_SKIN_LDU existed, and they are the reason it does:
        # the volume rules ask how MUCH plastic is shared, and on a small part
        # a whole stud of it is not much. The skin rule asks how THICKLY.
        #
        # Taken from the project models on disk rather than invented, which is
        # why the numbers are odd: these are placements the agent really wrote.
        ("a 2x4 brick a stud inside a 2x2 slope", False,
         "1 4  0   0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 20 -24 0 1 0 0 0 1 0 0 0 1 3039.dat\n"),
        # Two of the four slopes that ring a tower's roof, turned to face
        # outwards. Copied verbatim from the model rather than placed by hand:
        # the fault is in how the ring was laid out, and a hand-written pair
        # that merely stands side by side does not reproduce it.
        ("two turned 1x3 slopes sharing a stud", False,
         "1 70 -10 -240 30 0 0 -1 0 1 0 1 0 0 4286.dat\n"
         "1 70  30 -240 -10 -1 0 0 0 1 0 0 0 -1 4286.dat\n"),
        ("two 4x4 inverted dishes a stud apart", False,
         "1 4  0 0 0 1 0 0 0 1 0 0 0 1 3960.dat\n"
         "1 2 20 0 0 1 0 0 0 1 0 0 0 1 3960.dat\n"),
        # The other side of the same rule, and the tighter one: the smallest
        # real defect it catches shares 562 cubic LDU and the bracket above —
        # which must pass — shares 435. Both sit inside SHARED_SKIN_MIN_LDU3's
        # 1.29x band, so any change to that number has to run these together.
        ("a round tile driven into a headlight brick's side stud", False,
         "1 15 -50 -48 -20 0 0 1 0 1 0 -1 0 0 87087.dat\n"
         "1 15 -50 -38 -20 0 1 0 0 0 -1 -1 0 0 98138.dat\n"),
    ]
    # KNOWN GAP, recorded rather than asserted, because a test that passes for
    # the wrong reason is worse than none.
    #
    #     1 7   0   0   0 ... 3958.dat      a 6x6 plate
    #     1 4 -30  -8 -30 ... 3003.dat      a 2x2 brick, correctly seated
    #     1 2  30  -8 -20 ... 3003.dat      a 2x2 brick, HALF A STUD OFF in z
    #     1 1  30 -32 -20 ... 3003.dat      another stacked on that one
    #
    # This used to fail, and it failed on `overlapping_parts` — which was the
    # old collision check reporting a plate against a brick correctly sitting
    # on it. The verdict was right and the reason was a false positive, and
    # when occupancy.py removed the false positive the case stopped failing.
    #
    # Measured, the misplaced brick shares 66 cubic LDU with the plate and a
    # correctly placed one shares 36. Thirty cubic LDU is not a signal any
    # threshold can use — the worst false positive in the corpus is 390 — and
    # the reason is real rather than a limit of the method: a 2x2 brick's
    # underside is a large open cavity, so a stud landing half a stud off
    # barely grazes the tube. In real bricks it will not seat; in geometry it
    # very nearly does.
    #
    # So this is not a collision at all, it is a SEATING fault: the brick's
    # anti-studs do not line up with the studs under it. That belongs to the
    # connectivity checker, which currently calls it UNVERIFIED rather than
    # MISALIGNED — `classify`'s documented blind spot, whose safety argument
    # was "the model fails anyway", and it was this collision false positive
    # doing the failing. Closing it means making `seat_miss` fire here.
    # The gap that used to be recorded here — two 2x2 slopes a stud apart
    # sharing solid plastic and validating clean, and the same for wedges,
    # brackets and corner plates — is closed, and it is the first three cases
    # above. It is worth keeping the reason it was open: the check judged a
    # bounding box and then consulted a list of words about whether the box
    # could be trusted, which exempted 98% of the catalogue. See occupancy.py.
    #
    # SECOND KNOWN GAP, same rule: recorded rather than asserted.
    #
    #     1 4 0  0 0 ... 3001.dat       a 2x4 brick
    #     1 2 0 16 0 ... 54200.dat      a cheese slope sunk 8 LDU into it
    #
    # Unbuildable, and it validates clean. It shares 289 cubic LDU, 15.7% of
    # the cheese slope — and a 1x1 plate *correctly* carried on a bracket's
    # upstand (the case two above) shares 435, which is 19.7% of the plate. The
    # correct connection scores higher on both readings than the defect does,
    # so neither the volume nor the share separates them and no setting of
    # collisions.SHARED_FRACTION / FRACTION_MIN_LDU3 can close this without
    # reopening the bracket as a false positive.
    #
    # What it needs is a measurement that tells a connection interface from an
    # interpenetration. The voxel count cannot: a stud inside a tube and a
    # wedge inside a brick are both "plastic in the same cells", and on a part
    # as small as a 1x1 the skin left by the first is as big as the second.
    # Knowing which stud pairs with which anti-stud is the connectivity
    # checker's knowledge, not this one's, and joining them is the way through.

    # Against `validation.validate` rather than the tool, deliberately. The
    # tool repairs before it reports — an overlap that is pure arithmetic is
    # slid back onto the grid and the model then passes, which is the tool
    # doing its job and is tested in step 4. What is being pinned here is the
    # checker's *judgement*, which has to be right before there is anything
    # sound to repair towards.
    from .validation import validate

    path = _resolve_out("agent_selftest/buildable.ldr")
    path.parent.mkdir(parents=True, exist_ok=True)
    for name, must_pass, body in cases:
        path.write_text(head + body, encoding="utf-8")
        report = validate(path)
        passed = bool(report.get("passed"))
        print(f"   {'pass' if must_pass else 'fail'} expected — {name}: "
              f"{'PASS' if passed else 'FAIL'}")
        assert passed == must_pass, (
            f"{name}: expected {'pass' if must_pass else 'fail'}, got "
            f"{report.get('verdict') or report.get('error')}")
    path.unlink(missing_ok=True)


def _facing_check():
    """Turning a decoration piece must be told about, and must work.

    Two halves, and the second is the one with a trap in it.

    **Told about.** A slope, a wedge, a bracket and a printed tile have a
    direction, and which way they face is half of choosing them — so the
    search result says so. A 2x4 brick does not, and must stay quiet: a note
    on four parts in five is a note on none. This is deliberately *not* read
    off the corpus rotation share, which says a plain brick is turned 70% of
    the time — true, and only because a brick running along z carries a 90°
    matrix. See catalog.faces_a_direction.

    **Works.** Most LDraw slopes have their origin on their back stud row
    rather than at the centre of their footprint — `3039` runs z −30 to +10 —
    so a quarter turn moves where its studs land. The same slope that needs
    z+10 unturned needs x+10 at 90°. `build_ops` is what knows that; a
    rotation encouraged into a path that could not place it would be
    encouragement into a wall.
    """
    from . import catalog
    from .validation import validate

    for part, expected in (("3039", True), ("3037", True), ("11477", True),
                           ("2412b", True), ("3937", True),
                           ("3001", False), ("3024", False), ("2456", False),
                           ("3958", False), ("6141", False)):
        row = catalog.get_part(part)
        note = catalog.facing_note(row)
        print(f"   {part:7s} {'faces a direction' if note else 'no direction to choose':24s}"
              f"  {' '.join((row.get('description') or '').split())[:30]}")
        assert bool(note) == expected, (
            f"{part}: facing note {'missing' if expected else 'unwanted'}")

    # Every facing of every slope, asked for at the same spot, placed through
    # the tool the prompt points at. Each one has to land on the lattice and
    # validate — including the ones whose footprint the turn moved.
    path = _resolve_out("agent_selftest/facing.ldr")
    path.parent.mkdir(parents=True, exist_ok=True)
    base = ("0 FILE facing.ldr\n0 Facing\n0 Name: facing.ldr\n"
            "0 Author: LDraw Model Builder Agent\n0 !LDRAW_ORG Model\n\n"
            "1 71 0 0 0 1 0 0 0 1 0 0 0 1 3958.dat\n")
    for part in ("3039", "3040b", "11477"):
        turned = set()
        for degrees in (0, 90, 180, 270):
            path.write_text(base, encoding="utf-8")
            result = json.loads(call_tool("build_ops", {
                "path": "agent_selftest/facing.ldr",
                "ops": [{"op": "place", "part": part, "colour": 4,
                         "at": [0, -8, 0], "rotate": degrees}]}))
            assert not result.get("error"), (
                f"{part} at {degrees}°: {result.get('error')}")
            report = validate(path)
            assert report.get("passed"), (
                f"{part} at {degrees}° does not validate: {report.get('verdict')}")
            line = next(l for l in path.read_text().splitlines()
                        if l.startswith("1 ") and "3958" not in l)
            turned.add(" ".join(line.split()[5:14]))
        assert len(turned) == 4, (
            f"{part}: four facings produced {len(turned)} distinct matrices")
        print(f"   {part:7s} all four facings placed, aligned and validated")
    path.unlink(missing_ok=True)


def _floating_check():
    """A part with nothing holding it up is a fault; a part standing is not.

    Both halves, and the second is the one that took three attempts. Support
    spreads from the ground along **contact**, not along the stud graph: run
    over the stud graph this reported a floating part in 94.7% of the 1,819
    real sets, because clips, pins, brackets and side studs leave no edge in
    that graph and whole assemblies of a real set become their own island. And
    the ground is a band rather than a height, because a set whose lowest point
    is a tyre has its baseplate a plate above the ground. At the shipped
    settings the corpus reports 2.7% of sets and 0.49% of parts, and what is
    left is genuinely raised — a crane load, animals up a tree.
    """
    from .validation import validate

    head = ("0 FILE f.ldr\n0 Float\n0 Name: f.ldr\n"
            "0 Author: LDraw Model Builder Agent\n0 !LDRAW_ORG Model\n")
    cases = [
        # (name, floating expected, body)
        ("three bricks stacked", 0,
         "1 4 0   0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 4 0 -48 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        # A scene: two objects standing apart, each on its own ground.
        ("two objects side by side", 0,
         "1 4   0   0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 4   0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 200   0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 200 -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        ("one brick left in mid-air", 1,
         "1 4   0    0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 4   0  -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2 200 -120 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        # Joined to each other and to nothing else: both are flying, and the
        # spread has to carry the fault to the upper one rather than calling it
        # supported by the lower.
        ("a joined pair, both in the air", 2,
         "1 71   0    0 0 1 0 0 0 1 0 0 0 1 3958.dat\n"
         "1 2  200 -100 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
         "1 2  200 -124 0 1 0 0 0 1 0 0 0 1 3001.dat\n"),
        ("a roof with no walls under it", 1,
         "1 71 0   0 0 1 0 0 0 1 0 0 0 1 3958.dat\n"
         "1 4  0 -80 0 1 0 0 0 1 0 0 0 1 3020.dat\n"),
    ]

    path = _resolve_out("agent_selftest/floating.ldr")
    path.parent.mkdir(parents=True, exist_ok=True)
    for name, expected, body in cases:
        path.write_text(head + body, encoding="utf-8")
        found = validate(path).get("connectivity", {}).get("floating", 0)
        print(f"   {name}: {found} floating (expected {expected})")
        assert found == expected, f"{name}: {found} floating, expected {expected}"
    path.unlink(missing_ok=True)


def _critic_grounding_check():
    """What the visual critic is told, and who wins when it disagrees.

    No vision call: both halves of this are ordinary functions over a report.

    The critic runs only on a model that has already **passed** the grid check,
    which means connectivity is not an open question by the time it speaks — it
    is a fact on file. That changes what its answer is worth. Before this, a
    critic that misread a thin join as a gap produced "THIS IS NOT ONE BUILD …
    must be fixed before anything else", and the builder went and reattached
    parts that were never loose.

    The rule pinned here: the measurement owns connectivity, the critic owns
    everything a measurement has no opinion about, and a check that never ran
    claims nothing at all — which is the case that would otherwise turn this
    whole mechanism into the confident-sounding guess it exists to remove.
    """
    from .tools import _measured_facts, _reconcile

    passed = {"passed": True, "parts": 47,
              "size": {"size_studs": {"width": 8.0, "depth": 6.0,
                                      "height_bricks": 5.0}},
              "connectivity": {"objects_checked": "whole",
                               "objects_in_pieces": [], "floating": 0},
              "collision": {"overlapping": 0}}
    unchecked = {**passed,
                 "connectivity": {"objects_checked": None,
                                  "objects_in_pieces": [], "floating": 0}}
    split = {**passed,
             "connectivity": {"objects_checked": "whole", "floating": 0,
                              "objects_in_pieces": [{"object": "this model",
                                                     "pieces": 2}]}}

    facts = _measured_facts(passed)
    print(f"   measured facts: {len(facts.splitlines()) - 1} fact(s) offered")
    assert "8.0 studs wide" in facts, f"the size never reached the critic: {facts}"
    assert "EVERY PART IS JOINED" in facts, facts

    # The half that matters more: a check that did not run must not be reported
    # as a check that passed.
    quiet = _measured_facts(unchecked)
    print("   nothing measured about connectivity -> claimed: "
          f"{'JOINED' in quiet}")
    assert "JOINED" not in quiet, f"claimed a connectivity it never checked: {quiet}"
    assert _measured_facts({"passed": False}) is None, \
        "facts were offered for a model that failed the grid check"

    # Critic says split, geometry measured it whole: not a disconnection.
    seen = _reconcile({"one_build": False,
                       "separate_pieces": ["a clump of grass"]}, passed)
    print(f"   critic saw two clumps, geometry says joined -> "
          f"reconciled={seen.get('one_build_measured')}")
    assert seen.get("one_build_measured") and seen.get("one_build_note"), seen
    assert "NOT a disconnection" in seen["one_build_note"], seen

    # Critic says whole, geometry found pieces: the measurement wins outright,
    # because a gap can hide behind a nearer part.
    seen = _reconcile({"one_build": True}, split)
    print(f"   critic saw one build, geometry found pieces -> "
          f"one_build={seen['one_build']}")
    assert seen["one_build"] is False and seen.get("one_build_measured"), seen

    # Nothing measured: the critic is the only witness and keeps its verdict.
    seen = _reconcile({"one_build": False, "separate_pieces": ["a tree"]},
                      unchecked)
    print(f"   nothing measured -> the critic keeps its verdict: "
          f"one_build={seen['one_build']}, arbitrated={bool(seen.get('one_build_measured'))}")
    assert seen["one_build"] is False and not seen.get("one_build_measured"), seen

    # And nothing else the critic says is ever touched.
    original = {"one_build": True, "issues": [{"what": "the roof is too wide"}],
                "character": {"generic": True}}
    assert _reconcile(dict(original), passed) == original, \
        "reconciliation edited something that was not connectivity"
    print("   proportion, character and issues pass through untouched")


def _brief_sampling_check():
    """The brief asks for a distribution; this is the reading of the answer.

    No API call: every case here is a canned reply. What is being pinned is the
    part that decides *which* brief a build gets, and it has to hold up against
    replies that are not the shape that was asked for — because the reply shape
    is the one thing this pass cannot control, and the fallback for getting it
    wrong is silently building the median model.

    The tail rule is the load-bearing one. Taking the mode back would spend a
    five-candidate call to arrive exactly where asking once already was.
    """
    from . import brief

    wrapper = json.dumps({"briefs": [
        {"probability": 0.55, "brief": {"avoid": "a box", "reads_as": "obvious"}},
        {"probability": 0.20, "brief": {"avoid": "a box", "reads_as": "middle"}},
        {"probability": 0.05, "brief": {"avoid": "a box", "reads_as": "tail-a"}},
        {"probability": 0.05, "brief": {"avoid": "a box", "reads_as": "tail-b"}},
    ]})
    cases = [
        ("the shape asked for", wrapper, 4),
        # A preamble and a fence, which models add however firmly asked not to.
        ("fenced, with a preamble",
         "Here they are:\n```json\n" + wrapper + "\n```\nHope that helps!", 4),
        # Cut off mid-list: the outer object never closes, and the briefs inside
        # it are still perfectly good.
        ("truncated mid-list",
         wrapper[:wrapper.index('"tail-b"')] + '"tail-b"}}', 4),
        # Fields beside the probability rather than nested under `brief`.
        ("fields inline", json.dumps({"briefs": [
            {"probability": 0.7, "reads_as": "obvious", "avoid": "a box"},
            {"probability": 0.1, "reads_as": "tail", "avoid": "a box"}]}), 2),
        # One brief, which is what candidates=1 and a smaller model both give.
        ("a single bare brief",
         json.dumps({"reads_as": "just one", "avoid": "a box"}), 1),
        ("prose, no JSON at all", "I think a tree should look nice.", 0),
    ]
    for name, text, expected in cases:
        found = brief._candidates(text)
        print(f"   {name}: {len(found)} candidate(s) (expected {expected})")
        assert len(found) == expected, \
            f"{name}: {len(found)} candidates, expected {expected}"

    # An INVITED request takes the tail: the mode is never what comes back when
    # there is a tail to take.
    found = brief._candidates(wrapper)
    picked = {brief._select(found, seed=f"s{i}", allowed=brief.INVITED)[0]["reads_as"]
              for i in range(60)}
    print(f"   invited, over 60 seeds the tail yields: {sorted(picked)}")
    assert "obvious" not in picked, \
        f"the most likely brief was returned; the sampling is doing nothing: {picked}"
    assert len(picked) > 1, f"only ever one brief chosen: {picked}"

    # A PLAIN request takes the mode, every time and whatever the seed. This is
    # the half that answers "ask for a table and get a table": the tail brief is
    # the unusual one by construction, and a bare request never wanted it.
    plain = {brief._select(found, seed=f"s{i}", allowed=brief.PLAIN)[0]["reads_as"]
             for i in range(60)}
    print(f"   plain, over 60 seeds: {sorted(plain)}")
    assert plain == {"obvious"}, \
        f"a plain request did not get the most likely brief: {plain}"

    # And the same seed has to reach the same brief, or a resumed run quietly
    # redesigns what it already built.
    once = brief._select(found, seed="p:1", allowed=brief.INVITED)[0]["reads_as"]
    assert all(brief._select(brief._candidates(wrapper), seed="p:1",
                             allowed=brief.INVITED)[0]["reads_as"] == once
               for _ in range(10)), "the seeded choice is not stable"
    print(f"   one seed, ten draws: {once!r} every time")

    # Everything rated likely still has to yield the least likely of them.
    flat = brief._candidates(json.dumps({"briefs": [
        {"probability": 0.9, "brief": {"reads_as": "high"}},
        {"probability": 0.8, "brief": {"reads_as": "less-high"}}]}))
    chosen = brief._select(flat, seed="x", allowed=brief.INVITED)[0]["reads_as"]
    print(f"   nothing in the tail -> {chosen!r}")
    assert chosen == "less-high", f"expected the least likely, got {chosen}"
    assert brief._select(flat, seed="x", allowed=brief.PLAIN)[0]["reads_as"] == "high", \
        "a plain request did not take the mode when every candidate was likely"

    # What decides which of those two happens. The default is the one that
    # matters: a bare noun is a request for the thing that noun names.
    cases = [
        ("a table", "", brief.PLAIN),
        ("a small red car", "must have four wheels", brief.PLAIN),
        ("a house", "with a chimney and a blue door", brief.PLAIN),
        ("something creative", "", brief.INVITED),
        ("a table", "surprise me with it", brief.INVITED),
        ("an unusual lamp", "", brief.INVITED),
        ("a spaceship", "go wild", brief.INVITED),
    ]
    for subject, requirements, expected in cases:
        got = brief.licence(subject, requirements)
        print(f"   {subject!r} + {requirements!r} -> {got}")
        assert got == expected, \
            f"{subject!r} read as {got}, expected {expected}"

    # A picture is the specification, so it is plain however it was asked for —
    # a tail brief there fills the gaps as unlike the photograph as it can.
    assert brief.licence("something creative", "", {"subject": "a car"}) == brief.PLAIN, \
        "a reference picture did not force the plain reading"

    # And a plain request gets no angle and the fixed stance, which is where the
    # lopsided tables were coming from.
    assert brief.variation("p", brief.PLAIN) is None, \
        "a plain request was handed an angle to push on"
    assert brief.persona("p", brief.PLAIN) == brief.PLAIN_STANCE, \
        "a plain request was handed a drawn stance"
    print("   plain -> no angle, fixed stance; invited -> both drawn")

    # Numbers that are not a distribution, which is a thing models answer with.
    # Read literally, percentages put every candidate above any tail ceiling
    # and the sampling quietly stops happening.
    scales = [
        ("percentages", [45, 30, 15, 10]),
        ("unnormalised weights", [4.5, 3.0, 1.5, 1.0]),
        ("already a distribution", [0.45, 0.30, 0.15, 0.10]),
    ]
    for name, numbers in scales:
        rated = brief._candidates(json.dumps({"briefs": [
            {"probability": n, "brief": {"reads_as": f"b{i}"}}
            for i, n in enumerate(numbers)]}))
        got = [round(p, 3) for p, _ in rated]
        print(f"   {name}: {numbers} -> {got}")
        assert got == [0.45, 0.3, 0.15, 0.1], f"{name} rescaled to {got}"

    # A probability written inside the brief must be read and then removed:
    # left there it travels on into what the builder is handed.
    inside = brief._candidates(json.dumps({"briefs": [
        {"brief": {"probability": 0.7, "reads_as": "obvious"}},
        {"brief": {"probability": 0.1, "reads_as": "tail"}}]}))
    document, probability = brief._select(inside, seed="x", allowed=brief.INVITED)
    print(f"   probability written inside the brief -> p={probability}, "
          f"keys {sorted(document)}")
    assert probability == 0.1 and "probability" not in document, \
        f"probability not lifted out of the brief: {document}"

    # The two cue axes have to be independent, or eighty combinations is ten.
    pairs = {(brief.variation(f"p{i}", brief.INVITED),
              brief.persona(f"p{i}", brief.INVITED))
             for i in range(400)}
    angles = {a for a, _ in pairs}
    stances = {s for _, s in pairs}
    print(f"   400 seeds -> {len(angles)} angle(s), {len(stances)} stance(s), "
          f"{len(pairs)} distinct pairing(s)")
    assert len(pairs) > len(angles) + 4, \
        f"the angle and the stance move together: {len(pairs)} pairings"


def _connected_check():
    """One object must be one piece; separate objects must be left alone.

    The two halves are the whole point, and the second is the one that makes
    this hard. "Everything in the file is joined up" is trivial to check and
    fails every scene there is — a tree beside a car is two objects that are
    *meant* not to touch, and a checker that reports it has told the builder to
    glue them together.

    So what an object is gets declared by the harness rather than read off the
    file, and these cases pin both readings:

    * ``objects="whole"`` — a builder's own file. Everything in it belongs to
      the one object it was told to make, so two clumps is a fault.
    * ``objects="blocks"`` — an assembled scene. One block is one object; the
      blocks stand apart and only their insides have to hold together. It
      reports and does not fail, because "a block is an object" is an authoring
      convention rather than a fact — the OMR sets use blocks to mean
      *instruction step*, and on 250 of them this reading flags 6.4%.
    * ``objects=None`` — nobody said. The check does not run at all, which is
      what keeps every existing caller exactly where it was.

    Measured against the corpus at those settings: None and "blocks" both leave
    the pass rate at 82.4%, unmoved. Declaring a corpus set "whole" would reject
    20.4% of them, and that number is not a bug — it is what a *whole set* is. A
    sold set is a scene, and calling it one object is a false declaration. It is
    the reason this is declared by the harness that made the file and never
    guessed from the geometry.
    """
    from .validation import validate

    head = ("0 FILE c.ldr\n0 Connected\n0 Name: c.ldr\n"
            "0 Author: LDraw Model Builder Agent\n0 !LDRAW_ORG Model\n")
    stack = ("1 4 0   0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
             "1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
    # The same two bricks plus one standing well clear of them, on the ground so
    # that it is adrift rather than merely flying — this must fail for being
    # detached, not for being unsupported.
    split = stack + "1 2 0 0 300 1 0 0 0 1 0 0 0 1 3001.dat\n"
    # A scene: two objects, each a block, standing 200 LDU apart.
    scene = ("0 FILE scene.ldr\n0 Scene\n0 Name: scene.ldr\n"
             "1 16   0 0 0 1 0 0 0 1 0 0 0 1 tree.ldr\n"
             "1 16 400 0 0 1 0 0 0 1 0 0 0 1 car.ldr\n0 NOFILE\n"
             "0 FILE tree.ldr\n{tree}0 NOFILE\n"
             "0 FILE car.ldr\n" + stack)

    cases = [
        # (name, body, objects, must_pass, objects reported in pieces)
        ("a builder's own file, all joined up", head + stack, "whole", True, 0),
        ("a builder's own file, in two clumps", head + split, "whole", False, 1),
        # The one that a naive whole-file check gets wrong.
        ("a scene of two objects standing apart",
         scene.format(tree=stack), "blocks", True, 0),
        # Reported, but not a failure: see the docstring.
        ("a scene whose tree is in two halves",
         scene.format(tree=split), "blocks", True, 1),
        # And with nobody declaring anything, nothing is asked.
        ("an undeclared file in two clumps", head + split, None, True, 0),
    ]

    path = _resolve_out("agent_selftest/connected.ldr")
    path.parent.mkdir(parents=True, exist_ok=True)
    for name, body, objects, must_pass, expected in cases:
        path.write_text(body, encoding="utf-8")
        report = validate(path, objects=objects)
        passed = bool(report.get("passed"))
        apart = (report.get("connectivity") or {}).get("objects_in_pieces") or []
        print(f"   objects={str(objects):7} {name}: "
              f"{'PASS' if passed else 'FAIL'}, {len(apart)} in pieces "
              f"(expected {'pass' if must_pass else 'fail'}, {expected})")
        assert passed == must_pass, (
            f"{name}: expected {'pass' if must_pass else 'fail'}, got "
            f"{report.get('verdict') or report.get('error')}")
        assert len(apart) == expected, (
            f"{name}: {len(apart)} object(s) reported in pieces, "
            f"expected {expected}")
    path.unlink(missing_ok=True)


def _subassembly_gate_check():
    """A build may not end as a heap of clumps, however cleanly it validates.

    The model here is the case the fault checks cannot catch and this ceiling
    exists for: five 2x2 bricks in a row on the ground, each touching its
    neighbour's wall and seated on none of its studs. Every check passes — on
    the grid, nothing overlapping, nothing floating, and `objects_in_pieces`
    sees one piece because contact counts as joined there. It is still five
    loose bricks, and picking it up leaves four of them on the table.

    Both directions, because only one of them is obvious. A builder must be
    refused; the assembly pass must NOT be, since a scene is *meant* to come
    apart into one piece per object. See runstate.MAX_SUBASSEMBLIES.
    """
    from .runstate import MAX_SUBASSEMBLIES, RunState

    path = "agent_selftest/heap.ldr"
    count = MAX_SUBASSEMBLIES + 2
    _create(path, "\n".join(
        ["0 FILE heap.ldr", "0 Heap", "0 Name: heap.ldr", ""]
        + [f"1 4 {40 * i} 0 0 1 0 0 0 1 0 0 0 1 3003.dat"
           for i in range(count)]) + "\n")

    for scope, may_finish in (("whole", False), ("blocks", True)):
        state = RunState(subject="a heap of bricks", project="agent_selftest",
                         target=path, require_render=False, objects=scope)
        state.record_write(path)
        report = json.loads(call_tool("validate_model",
                                      {"path": path, "grid_only": True},
                                      state=state))
        pieces = (report.get("connectivity") or {}).get("subassemblies")
        loose = (report.get("connectivity") or {}).get("loose_pieces") or []
        finished = json.loads(call_tool("finish", {"summary": "done"},
                                        state=state))
        print(f"   objects={scope:7} passes validation={report.get('passed')}, "
              f"{pieces} subassemblies, {len(loose)} loose piece(s) listed -> "
              f"finish {'accepted' if finished['finished'] else 'refused'}")
        assert report.get("passed"), \
            f"the model this checks with does not validate: {report.get('verdict')}"
        assert pieces == count, \
            f"expected {count} subassemblies, the checker read {pieces}"
        assert len(loose) == count - 1, \
            f"{len(loose)} clumps listed beside the main body, expected {count - 1}"
        assert finished["finished"] is may_finish, (
            f"objects={scope}: finish was "
            f"{'accepted' if finished['finished'] else 'refused'} on a model in "
            f"{pieces} pieces")
        if not may_finish:
            assert any("separate pieces" in p for p in finished["problems"]), \
                f"refused, but not for the pieces: {finished['problems']}"

    # ...and at the ceiling exactly, a builder is let through: this is a
    # `>` and an off-by-one here would move the limit without anyone noticing.
    _create(path, "\n".join(
        ["0 FILE heap.ldr", "0 Heap", "0 Name: heap.ldr", ""]
        + [f"1 4 {40 * i} 0 0 1 0 0 0 1 0 0 0 1 3003.dat"
           for i in range(MAX_SUBASSEMBLIES)]) + "\n")
    state = RunState(subject="a heap of bricks", project="agent_selftest",
                     target=path, require_render=False, objects="whole")
    state.record_write(path)
    call_tool("validate_model", {"path": path, "grid_only": True}, state=state)
    at_limit = json.loads(call_tool("finish", {"summary": "done"}, state=state))
    print(f"   exactly {MAX_SUBASSEMBLIES} subassemblies -> "
          f"finish accepted: {at_limit['finished']}")
    assert at_limit["finished"], \
        f"the ceiling itself was refused: {at_limit.get('problems')}"

    Path(_resolve_out(path)).unlink(missing_ok=True)


def _requirements_check():
    """The acceptance checklist: written, stored, and what it refuses.

    Offline throughout — no network and no vision model. What is pinned
    here is the half that decides whether a run may end: that a vague
    criterion never reaches the list, that an unanswered one counts as
    false rather than as silence, and that `finish` can no longer end a
    build by declaring it done. See requirements.py.
    """
    import json
    import shutil

    from . import requirements as R
    from .config import OUT_DIR
    from .runstate import RunState

    PROJECT = "__selftest_requirements"

    print("1. objectivity filter")
    for text, want in [
        ("The table has exactly four legs", True),
        ("The table top is between 4 and 8 studs wide", True),
        ("Every leg touches both the top and the ground", True),
        ("The table looks good", False),
        ("Realistic proportions", False),
        ("The model has a nice colour scheme", False),
        ("Sufficient detail on the front face", False),
        ("The build is well-proportioned", False),
        ("no", False),
    ]:
        got = R.is_objective(text)
        print(f"   {'ok  ' if got == want else 'MISS'} {str(got):5} {text!r}")
        assert got == want, text

    print("1b. nothing is invented: colour is free unless it was asked for")
    for text, want in [
        ("The seat, back and arms are all white", True),
        ("There are exactly two red cushions on the seat", True),
        ("The model is built in a consistent palette", True),
        ("The model is a sofa with a seat and a back", False),
        ("The sofa has exactly two armrests", False),
    ]:
        got = R.mentions_colour(text)
        print(f"   {'ok  ' if got == want else 'MISS'} colour={str(got):5} {text!r}")
        assert got == want, text

    # The whole point: with no colour asked for, a colour requirement is thrown
    # out rather than allowed to refuse a finished model for being the wrong
    # shade of something nobody specified. This is the sofa run, exactly.
    kept, dropped, invented = R._normalise({"requirements": [
        {"text": "The model is a sofa with a seat, a back and two arms"},
        {"text": "The seat, back and arms are all white"},
        {"text": "There are exactly two red cushions on the seat"},
        {"text": "The model is one connected object"},
    ]}, colour_asked=False)
    print(f"   no colour asked -> kept {len(kept)}, not asked for {len(invented)}")
    assert len(kept) == 2 and len(invented) == 2, (kept, invented)
    assert not any(R.mentions_colour(r["text"]) for r in kept)

    # ...and when it WAS asked for, the same list survives intact.
    kept, _, invented = R._normalise({"requirements": [
        {"text": "The model is a fire engine with a body and wheels"},
        {"text": "The body of the fire engine is red"},
    ]}, colour_asked=True)
    print(f"   colour asked    -> kept {len(kept)}, not asked for {len(invented)}")
    assert len(kept) == 2 and not invented, (kept, invented)

    print("2. normalise: renumbers, keeps the objective, records the rest")
    kept, dropped, _ = R._normalise({"requirements": [
        {"text": "The model is a table", "check": "visual"},
        {"text": "It looks nice", "check": "visual"},
        {"text": "The table has exactly four legs", "check": "visual"},
        {"text": "The model is one connected object", "check": "measured"},
        {"text": "Adequate sturdiness"},
    ]})
    print(f"   kept {[r['id'] for r in kept]}, dropped {len(dropped)}")
    assert [r["id"] for r in kept] == ["r1", "r2", "r3"], kept
    assert len(dropped) == 2, dropped
    assert kept[2]["check"] == "measured"

    print("2b. symmetry: the mirror is kept, the demand for irregularity is not")
    for text, forbidden in [
        # a requirement nobody asked for: it refuses a rock that came out tidy
        ("The rock is not symmetric about any plane", True),
        ("The rock has no symmetry about any plane", True),
        ("The wall is asymmetric and irregular", True),
        ("The tree avoids symmetry", True),
        ("The model is free of mirror symmetry", True),
        # ...and the positive form, which is the point of the whole section
        ("The car's left and right sides mirror each other about its length", False),
        ("The walls and roof are symmetric about the centre line, apart from "
         "the door and the chimney", False),
        ("The two front windows are at the same height, with no gap between them", False),
        ("The model is symmetric and has no visible studs on the roof", False),
        ("All four legs are the same height", False),
    ]:
        got = R.forbids_symmetry(text)
        assert got == forbidden, f"{text!r} -> {got}, wanted {forbidden}"
    print("   5 demands for irregularity dropped, 5 mirrors kept")

    # ...and the same through _normalise, where it is actually applied.
    kept_s, _, invented_s = R._normalise({"requirements": [
        {"text": "The rock is one connected object", "check": "measured"},
        {"text": "The rock is not symmetric about any plane", "check": "visual"},
    ]})
    assert len(kept_s) == 1 and invented_s, (kept_s, invented_s)
    print(f"   and through _normalise: kept {len(kept_s)}, "
          f"rejected as not asked for {len(invented_s)}")

    print("3. persistence round-trip")
    record = {"subject": "a table", "requirements": kept}
    R.save(PROJECT, "table", record)
    back = R.for_object(PROJECT, "table")
    print(f"   stored and read back {len(R.items(back))} requirement(s)")
    assert [r["id"] for r in R.items(back)] == ["r1", "r2", "r3"]
    R.save(PROJECT, "chair", {"requirements": [dict(kept[0])]})
    assert set(R.load(PROJECT)) == {"table", "chair"}, "saving one lost the other"
    print("   a second object did not overwrite the first")

    print("4. missing answers count as false, never as silence")
    answers = R._parse(json.dumps({"results": [
        {"id": "r1", "met": True, "evidence": "seen in HOME"},
        {"id": "r2", "met": False, "evidence": "only three legs"},
    ]}), ["r1", "r2", "r3"])
    print(f"   r3 was never answered -> met={answers['r3'][0]}")
    assert answers["r1"][0] is True and answers["r2"][0] is False
    assert answers["r3"][0] is False, "an unanswered requirement passed"

    print("5. `finish` cannot end a run that has a checklist")
    state = RunState(subject="a table", project=PROJECT,
                     target=f"{PROJECT}/m.ldr", require_render=False)
    state.requirements = record
    state.record_write(f"{PROJECT}/m.ldr")
    state.record_validation(f"{PROJECT}/m.ldr", {"passed": True})
    out = json.loads(call_tool("finish", {"summary": "done"}, state=state))
    print(f"   finish -> finished={out['finished']}  why={out.get('why','')[:58]}...")
    assert out["finished"] is False, "finish ended a run with requirements outstanding"
    assert "validate_model" in json.dumps(out), "the refusal does not say what to do"

    print("6. give_up is still an honest way out")
    out = json.loads(call_tool("finish", {"summary": "stuck", "give_up": True,
                                          "blocked_by": "the part does not exist"},
                               state=state))
    print(f"   give_up -> finished={out['finished']} gave_up={out.get('gave_up')}")
    assert out["finished"] and out["gave_up"], out

    print("7. the gate reports the checklist, not just generic faults")
    state2 = RunState(subject="a table", project=PROJECT,
                      target=f"{PROJECT}/m.ldr", require_render=False)
    state2.requirements = record
    state2.record_write(f"{PROJECT}/m.ldr")
    state2.record_validation(f"{PROJECT}/m.ldr", {"passed": True})
    ok, problems, nxt = state2.gate()
    print(f"   never checked -> ok={ok}: {problems[0][:64]}...")
    assert not ok and "have not been checked" in problems[0]

    state2.record_requirements_check(f"{PROJECT}/m.ldr", {
        "passed": False, "met": [kept[0]], "unmet": [kept[1]]})
    ok, problems, nxt = state2.gate()
    assert not ok and "not met" in " ".join(problems)
    print(f"   one unmet   -> ok={ok}: {[p[:44] for p in problems][0]}...")

    state2.record_requirements_check(f"{PROJECT}/m.ldr", {
        "passed": True, "met": kept, "unmet": []})
    ok, problems, nxt = state2.gate()
    print(f"   all met     -> ok={ok}")
    assert ok, problems

    print("8. as_text / outstanding render for the prompt")
    assert "r1" in R.as_text(record)
    text = R.outstanding({"unmet": [dict(kept[1], evidence="only three legs")]})
    assert "r2" in text and "only three legs" in text
    print("   both render")

    print("8b. requirements answered from the .ldr file, not from a picture")
    _source_check(R)

    print("9. the check is compulsory, and only a clean build ends the run")
    _gate_check(R, record)

    shutil.rmtree(OUT_DIR / "projects" / PROJECT, ignore_errors=True)


def _cited_parts_check():
    """Every part number the standing prompt hands out must be a real part.

    The prompt tells the builder to place these **without looking them up** —
    that is the whole point of the table, and it is what makes a wrong number
    there worse than no table at all: it would be placed on trust, in every
    build, until somebody noticed the renders were missing a wall.

    Numbers go stale on their own, without anyone editing the prompt: LDraw
    retires a part and the catalogue starts describing it as "~Moved to". So
    this is checked rather than reviewed. `3023` is cited on purpose, as the
    stub you are told *not* to use.
    """
    import re

    from . import catalog
    from .config import CONTEXT_DIR

    from .config import PARTS_DIR

    # `3023` is cited on purpose, as the stub you are told *not* to use.
    CITED_AS_A_WARNING = {"3023"}
    # Not part numbers at all: `20w`, `10w` and `20g` are the arithmetic in the
    # LDU section — 20 times the width in studs — and they match the shape of a
    # part id closely enough to be picked up here.
    NOT_PART_NUMBERS = {"10w", "20w", "20g"}

    total, missing, retired = 0, [], []
    for path in sorted(CONTEXT_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for part in sorted(set(re.findall(r"`([0-9][0-9a-zA-Z]{2,7})`", text))):
            if part in CITED_AS_A_WARNING or part in NOT_PART_NUMBERS:
                continue
            total += 1
            row = catalog.get_part(part)
            in_library = (PARTS_DIR / f"{part}.dat").is_file()
            if not row and not in_library:
                # Nothing anywhere: a typo or an invented number, and it would
                # be placed on trust in every build that read this table.
                missing.append(f"{part} ({path.name})")
                continue
            description = (row or {}).get("description") or ""
            if description.startswith("~") or (row or {}).get(
                    "category") in ("Moved", "Obsolete"):
                # Retired in the catalogue but still a real file the renderer
                # resolves — 3815/3816/3817, the classic minifig hips and legs,
                # are "Obsolete" and still placed 1,075 times across 501 sets.
                # Worth knowing about, not worth failing a build over.
                retired.append(f"{part} ({path.name}): {description[:40]}")
    print(f"   {total} part numbers cited across the context blocks")
    assert not missing, ("the prompt hands out parts that exist nowhere:\n  "
                         + "\n  ".join(missing))
    print("   every one resolves in the catalogue or the LDraw library")
    if retired:
        print(f"   {len(retired)} retired but still usable: "
              + "; ".join(r.split(":")[0] for r in retired))


def _context_check():
    """What reaches the builder and the critic, and what is kept out of it.

    Three separate leaks, all measured on the 27-step tiny-house run on disk:
    the critic was handed the checklist as a Python dict repr, nothing ever
    left the builder's conversation (30,110 tokens of tool output by the end),
    and the standing prompt shipped whole whatever was being built.
    """
    import json as _json

    from . import prompts, render, tools as tool_module
    from .agent import KEEP_TOOL_RESULTS, LDrawAgent, _condense

    print("1. the critic reads a checklist, not a dict repr")
    record = {"subject": "a tiny house", "written_at": 1.0, "requirements": [
        {"id": "r1", "text": "The roof slopes or peaks rather than being flat",
         "check": "visual", "why": "a house has a roof"},
        {"id": "r2", "text": "The model is one connected object",
         "check": "measured", "why": "it has to survive being picked up"}]}
    text = render._as_requirements(record)
    assert "The roof slopes" in text and "'check':" not in text, text
    assert "written_at" not in text and "why" not in text, text
    print(f"   {len(_json.dumps(record))} chars of record -> {len(text)} of list, "
          f"no ids, no why, no timestamp")

    brief = {"reads_as": "a compact cottage with a steep gable roof"}
    assert "gable" in render._as_brief(brief), "the brief does not reach the critic"
    print("   and the design brief reaches it, which it never used to")

    print("2. the conversation stops growing")
    agent = LDrawAgent.__new__(LDrawAgent)
    agent.messages = []
    big = _json.dumps({"verdict": "PASS", "results": [
        {"part_id": f"30{i:02d}", "description": "Brick " * 20} for i in range(20)]})
    for i in range(KEEP_TOOL_RESULTS + 4):
        agent.messages.append({"role": "assistant", "content": "",
                               "tool_calls": [{"id": f"c{i}"}]})
        agent.messages.append({"role": "tool", "tool_call_id": f"c{i}",
                               "name": "search_parts", "content": big})
        agent._prune_history()
    tools_now = [m for m in agent.messages if m["role"] == "tool"]
    pruned = [m for m in tools_now if "pruned" in m["content"]]
    assert len(tools_now) == KEEP_TOOL_RESULTS + 4, "a tool result went missing"
    assert len(pruned) == 4, f"{len(pruned)} pruned, expected 4"
    # Every call still has its answer, or the next request is rejected outright.
    answered = {m["tool_call_id"] for m in tools_now}
    assert len(answered) == KEEP_TOOL_RESULTS + 4, "a tool call lost its result"
    keeps = _json.loads(pruned[0]["content"])
    assert keeps.get("verdict") == "PASS", "the verdict was dropped"
    assert len(keeps.get("part_ids") or []) == 20, "the part numbers were dropped"
    print(f"   {len(tools_now)} results, {len(pruned)} shortened, "
          f"{len(tools_now) - len(pruned)} kept whole; every call still answered")
    print(f"   a shortened search keeps its verdict and all "
          f"{len(keeps['part_ids'])} part numbers")

    # The plan is what the build is following, however far back it falls.
    agent.messages = []
    agent.messages.append({"role": "tool", "tool_call_id": "p", "content": big,
                           "name": "plan_construction"})
    for i in range(KEEP_TOOL_RESULTS + 2):
        agent.messages.append({"role": "tool", "tool_call_id": f"x{i}",
                               "name": "search_parts", "content": big})
    agent._prune_history()
    assert "pruned" not in agent.messages[0]["content"], "the plan was pruned"
    print("   and plan_construction is never shortened")

    print("3. the standing prompt leaves out what the build cannot need")
    tool_module.set_copy_from_set(True)
    whole = prompts.build_system_prompt()
    letter = prompts.build_system_prompt(subject="the letter M, big and blocky")
    figure = prompts.build_system_prompt(subject="a minifigure firefighter")
    assert "<minifigures>" in whole, "nothing to drop"
    assert "<minifigures>" not in letter, "the figure block survived a letter"
    assert "<minifigures>" in figure, "a minifigure build lost its figure block"
    assert "<minifigures>" in prompts.build_system_prompt(subject="a car with a driver")
    print(f"   a letter: {len(whole) - len(letter):,} chars lighter; "
          f"a minifigure keeps every block")
    tool_module.set_copy_from_set(False)
    ungrafted = prompts.build_system_prompt(subject="the letter M")
    assert "<reference_sets>" not in ungrafted, "grafting guidance survived it being off"
    assert "<grafting_is_off>" in ungrafted
    tool_module.set_copy_from_set(True)
    print(f"   grafting off: {len(whole) - len(ungrafted):,} chars lighter, "
          f"and no section left contradicting another")


def _source_check(R):
    """The parts-list route: code counts, and only the words go to a model.

    Three things are pinned, and the third is the one that matters. A count is
    exact, so a criterion about *what the model is made of* is answered without
    a model at all; a criterion about *where a part went* is not answerable
    from a parts list at any price and must fall through to the pictures, or
    the gate passes a heap of correctly-coloured bricks as a finished build.
    """
    import shutil

    from .buildir import compile_ops
    from .config import OUT_DIR

    path = OUT_DIR / "__selftest_source" / "m.ldr"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = compile_ops([
        {"op": "wall", "colour": 4, "at": [0, 0, 0], "axis": "x",
         "length_studs": 12, "courses": 2, "note": "front wall"},
        {"op": "row", "part": "3070b", "colour": 14, "at": [0, -48, 0],
         "count": 6, "axis": "x", "note": "coping along the wall top"}])
    path.write_text("0 s\n0 Name: m.ldr\n" + "\n".join(lines) + "\n")

    stock = R.inventory(path)
    assert stock and stock["parts"] == len(
        [l for l in lines if l.startswith("1 ")]), stock
    reds = next(r["count"] for r in stock["by_colour"] if r["code"] == 4)
    print(f"   counted {stock['parts']} parts, {stock['distinct_colours']} "
          f"colours, {reds} in red — exactly, from the file")

    # Exact, and free: no model is asked anything.
    assert R.settle_source("the model uses exactly 2 colours", stock)[0]
    assert not R.settle_source("the model uses at least 5 colours", stock)[0]
    assert R.settle_source("exactly 6 of part 3070b", stock)[0]
    print("   counts and colours settled with no model call at all")

    # A criterion needing more than arithmetic is not settled by arithmetic —
    # it falls through to the model, which is shown the file and can read it.
    assert R.settle_source("the tiles sit on top of the wall", stock) is None
    print("   an arrangement criterion is left to the model, not counted")

    # The file's own section labels: this is what lets a criterion about a
    # named feature be answered from the source at all.
    named = R.sections(path)
    assert named and any(row["label"] for row in named), named
    assert any(p["part_id"] == "3070b" for row in named for p in row["parts"])
    print(f"   {len(named)} labelled section(s) read from the file's comments: "
          f"{', '.join(repr(r['label']) for r in named[:2])}")

    # ...and the whole file goes to the checker, coordinates and all.
    body = R.source_text(path)
    assert body and "3070b.dat" in body and "|" in body, body[:200]
    print("   the file itself is handed over, numbered, coordinates included")

    # Only how a thing *looks* still needs eyes.
    kept, _, _ = R._normalise({"requirements": [
        {"text": "the wall is red", "check": "source"},
        {"text": "the model reads as a house at a glance", "check": "source"}]})
    kinds = {r["text"]: r["check"] for r in kept}
    assert kinds.get("the wall is red") == "source", kinds
    assert kinds.get("the model reads as a house at a glance") == "visual", kinds
    print('   "the wall is red" -> source; "reads as a house" -> visual')

    shutil.rmtree(path.parent, ignore_errors=True)


def _gate_check(R, record):
    """LDrawAgent._requirements_gate: when it runs, and when it may end a run.

    Measured on the runs on disk before this was pinned: 38% of recent
    iterations ended without the checklist ever being put to the model, and
    every one of those skips was silent. So what is asserted here is as much
    that the check *happened* as what it concluded.

    Offline: the vision checker is replaced by a fixed verdict, which is the
    only part of the gate that would otherwise need a model.
    """
    from .agent import CRITIQUE_ROUNDS, LDrawAgent
    from .runstate import RunState

    passed = {"passed": True, "checked": 2, "met": [{"id": "r1"}, {"id": "r2"}],
              "unmet": [], "summary": "every requirement met"}
    path = "__selftest_gate/m.ldr"

    def agent(verdict=passed, grid=True, issues=(), requirements=record,
              rendered=True):
        state = RunState(subject="a table", project="__selftest_gate",
                         target=path, require_render=False)
        state.requirements = requirements
        state.record_write(path)
        state.record_validation(path, {"passed": grid,
                                       "verdict": "PASS" if grid else "FAIL - off grid"})
        if rendered:
            state.record_render(path, [], sheet="sheet.jpg")
        if issues:
            state.record_critique(path, {"issues": list(issues)})
        it = LDrawAgent.__new__(LDrawAgent)
        it.state, it.messages, it.transcript = state, [], []
        it.verbose, it.on_event, it._critique_rounds = False, None, 0
        it._last_tools = [("validate_model", '{"passed":true}')]
        R.check = lambda *a, **k: verdict
        return it

    real_check = R.check
    try:
        ends = lambda it: bool((it._requirements_gate(1) or {}).get("finished"))

        assert ends(agent()), "a clean build that met every requirement did not end"
        print("   requirements met, grid clean, critic quiet -> the run ends")

        # The one that used to skip: no contact sheet meant no check at all,
        # and the grid failing is exactly when there was no sheet.
        it = agent(grid=False)
        assert not ends(it), "a model off the stud grid ended the run"
        assert "does not pass the stud-grid check" in it.messages[-1]["content"]
        print("   requirements met but off the grid -> checked anyway, run continues")

        # A met checklist ends the run, and the critic does not get a veto over
        # it. This is the behaviour CRITIQUE_ROUNDS = 0 buys, and it is pinned
        # here because it is the whole contract: if every requirement is met,
        # the run finishes rather than opening another iteration.
        it = agent(issues=["the roof is a flat slab, not a roof"])
        assert ends(it), \
            "every requirement was met and the run did not finish"
        print("   requirements met, critic still objects -> the run still ends")

        # The critique is not lost by ending — it travels out with the result.
        assert (it.state.critiques.get(path) or {}).get("critique"), \
            "the critique was dropped instead of being reported"
        print("   ...and the critique goes out with the result, as a remark")

        # It is still possible to buy the holding rounds back for a session.
        if CRITIQUE_ROUNDS:
            it = agent(issues=["the roof is a flat slab, not a roof"])
            held = sum(0 if ends(it) else 1 for _ in range(CRITIQUE_ROUNDS + 1))
            assert held == CRITIQUE_ROUNDS, \
                f"the critique hold is not bounded: {held}"
            print(f"   LDRAW_CRITIQUE_ROUNDS={CRITIQUE_ROUNDS} -> bounded at that")

        # Every remaining way to not run it says so out loud.
        for name, kwargs in (("no criteria were written", {"requirements": None}),
                             ("nothing rendered to judge", {"rendered": False})):
            it = agent(**kwargs)
            said = []
            it._emit = lambda t, **f: said.append((t, f.get("reason")))
            assert it._requirements_gate(1) is None
            assert said and said[0][0] == "requirements_skipped", \
                f"{name} skipped the check silently"
            print(f"   {name} -> announced as {said[0][1]!r}")
    finally:
        R.check = real_check


def _side_stud_check():
    """A part on a SNOT brick's side stud connects at every rotation of the host.

    The bug this pins: `stud_map` reports a side stud in the part's OWN frame,
    where a 1x1 with a stud on one side always faces `-z`. Turn that brick a
    quarter turn and the stud faces `-x` in the model while the catalogue still
    said `-z` — so the part built onto it went against a face that was not
    there. Unrotated hosts connected and turned ones did not, which is exactly
    what it looked like from outside: "some of them connect and the others do
    not".

    Two numbers have to be right and both are checked here by building the
    model and asking the connectivity checker, not by comparing matrices:

    * the facing, which is the host's rotation applied to the stud's own axis
    * the stand-off, which is the attaching part's own stacking height along
      that facing — 8 for a plate, 24 for a brick. Its origin at the stud
      instead puts it inside the host, which is what the first attempt did.
    """
    from . import catalog
    from .validation import validate

    identity = "1 0 0 0 1 0 0 0 1"
    path = _resolve_out("agent_selftest/sidestud.ldr")
    path.parent.mkdir(parents=True, exist_ok=True)

    for host, added, expect in (("87087.dat", "3024.dat", "-z"),
                                ("87087.dat", "3005.dat", "-z"),
                                ("30414.dat", "3024.dat", "-z")):
        seen = []
        for name, matrix in catalog._TURNS:
            at = (0, -8, 0)
            studs = catalog.side_studs_placed(host, matrix, at, attaching=added)
            assert studs, f"{host} reported no side studs at {name}"
            stud = studs[0]
            path.write_text("\n".join([
                "0 FILE sidestud.ldr", "0 s", "0 Name: sidestud.ldr",
                f"1 71 0 0 0 {identity} 3958.dat",
                f"1 4 {at[0]} {at[1]} {at[2]} "
                f"{catalog._matrix_text(matrix)} {host}",
                "1 14 %g %g %g %s %s" % (*stud["place_at"], stud["matrix"], added),
            ]) + "\n", encoding="utf-8")

            report = validate(path, objects="whole")
            connectivity = report["connectivity"]
            loose = {r["part"] for r in
                     (connectivity.get("unverified_parts") or [])
                     + (connectivity.get("misaligned_parts") or [])}
            seen.append(stud["faces"])
            assert added not in loose, (
                f"{host} turned {name}: the part on its side stud came back "
                f"unconnected at {stud['place_at']}")
            assert report["passed"], (
                f"{host} turned {name}: {report['verdict']}")

        print(f"   {host} + {added}: connects at all four turns, "
              f"stud faces {', '.join(seen)}")
        assert seen[0] == expect and len(set(seen)) == 4, (
            f"{host}: the stud faced {seen} — a turn did not move it")

    # ...and the number that was wrong the first time. The stand-off is the
    # attaching part's height, so a brick stands three times as far out as a
    # plate; hard-coding either is how one of them ends up inside the host.
    plate = catalog.side_studs_placed("87087.dat", None, (0, 0, 0),
                                      attaching="3024.dat")[0]
    brick = catalog.side_studs_placed("87087.dat", None, (0, 0, 0),
                                      attaching="3005.dat")[0]
    print(f"   stand-off: plate {plate['stands_off_ldu']:g} LDU, "
          f"brick {brick['stands_off_ldu']:g} LDU")
    assert plate["stands_off_ldu"] == 8.0 and brick["stands_off_ldu"] == 24.0

    # Without naming what is going on, there is no placement to give — the stud
    # position alone is where the plastic is.
    bare = catalog.side_studs_placed("87087.dat", None, (0, 0, 0))[0]
    assert "place_at" not in bare, \
        "a placement was offered without knowing what part it was for"
    print("   no attaching part named -> no place_at offered")

    path.unlink(missing_ok=True)


def _pagination_check():
    """The page arithmetic behind the parallel booklet render.

    No LPub3D and no display: this is the part that decides *which worker
    renders which pages*, and it is checked here because getting it wrong is
    invisible. A booklet missing page 43 is still a booklet, and nobody counts
    the pages of a PDF they asked for.

    Two things have to hold. Every page lands in exactly one range — no gaps,
    no page rendered twice. And `page_count` has to be exact *before* anything
    is rendered: a range that runs past the end does not stop there, it pads.
    Asked for pages 5-9999 of an 18-page document, LPub3D rendered 9,995 blank
    ones. That is what `_drop_empty_steps` is for — a step with no parts in it
    makes no page, and every one of those made the count wrong by one.
    """
    import re

    from .. import instructions

    for pages in (1, 5, 12, 18, 43, 92, 199):
        for workers in (1, 2, 3, 4, 8):
            ranges = instructions._ranges(pages, workers)
            covered = [p for first, last in ranges for p in range(first, last + 1)]
            assert covered == list(range(1, pages + 1)), (
                f"{pages} pages over {workers} workers covers {covered[:8]}…")
    print("   every page lands in exactly one range, 1-199 pages, 1-8 workers")

    assert instructions._worker_count(instructions.MIN_PAGES_TO_SPLIT - 1) == 1
    assert instructions._worker_count(92) > 1
    assert instructions._worker_count(92, 1) == 1
    print(f"   a short booklet stays in one process, a long one splits "
          f"{instructions._worker_count(92)} ways")

    # A step with nothing in it renders no page, so it must not survive into
    # the count. Both of these are real shapes: an author's marker with nothing
    # after it, and two in a row.
    written = "\n".join([
        "0 FILE m.ldr",
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat",
        "1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat",
        "0 STEP",
        "1 4 0 -48 0 1 0 0 0 1 0 0 0 1 3001.dat",
        "0 STEP", "0 STEP"])
    prepared = instructions.prepare(written)
    assert "0 STEP\n0 STEP" not in prepared, "two markers in a row survived"
    counted = instructions.page_count(prepared)
    assert counted == len(re.findall(r"^\s*0\s+STEP\s*$", prepared, re.M))
    print(f"   empty steps dropped; {counted} pages counted from {counted} markers")


def _minifig_check():
    """A minifigure is checked against the figure, not against the stud grid.

    Both directions matter and only one of them is obvious. A correct figure
    must pass and read as ONE piece — before this existed it came back as nine
    separate subassemblies. A broken one must fail, which is the half that was
    silently missing: every arrangement of these parts used to validate, head
    floating forty LDU above the neck included.
    """
    def figure(head=-56, torso=-32, legs=12):
        return "\n".join([
            "0 FILE mf.ldr", "0 Minifig", "0 Name: mf.ldr",
            f"1 14 0 {head} 0 1 0 0 0 1 0 0 0 1 3626b.dat",
            f"1  4 0 {torso} 0 1 0 0 0 1 0 0 0 1 973.dat",
            f"1  4 0 {torso + 9} 0 1 0 0 0 1 0 0 0 1 3818.dat",
            f"1  4 0 {torso + 9} 0 1 0 0 0 1 0 0 0 1 3819.dat",
            "1  1 0 0 0 1 0 0 0 1 0 0 0 1 3815.dat",
            f"1  1 0 {legs} 0 1 0 0 0 1 0 0 0 1 3816.dat",
            f"1  1 0 {legs} 0 1 0 0 0 1 0 0 0 1 3817.dat",
            f"1  0 0 {head} 0 1 0 0 0 1 0 0 0 1 3901.dat",
        ]) + "\n"

    path = "agent_selftest/minifig.ldr"
    _create(path, figure())
    good = json.loads(call_tool("validate_model", {"path": path, "grid_only": True}))
    figures = good.get("minifigures") or {}
    conn = good.get("connectivity") or {}
    print(f"   a correct figure -> passed={good.get('passed')}, "
          f"{figures.get('assembled')}/{figures.get('found')} assembled, "
          f"{conn.get('subassemblies')} piece(s), "
          f"{conn.get('unverified')} unverified")
    assert good.get("passed"), f"a correct minifigure failed: {good.get('verdict')}"
    assert conn.get("subassemblies") == 1, \
        f"a correct minifigure read as {conn.get('subassemblies')} pieces"
    assert not conn.get("unverified"), \
        "a checked minifigure part is still being reported as unverified"

    # The figure the standing prompt tells the agent to copy, lifted out of the
    # prompt file itself and put through the checker. A worked example is a
    # promise, and one that does not validate teaches the agent to build a
    # broken minifigure every time — so the promise is tested rather than
    # proof-read.
    import re
    from .config import CONTEXT_DIR

    doc = (CONTEXT_DIR / "27_minifigures.md").read_text(encoding="utf-8")
    block = re.search(r"```\n(1 .*?)```", doc, re.S)
    assert block, "the minifigure prompt no longer contains a worked example"
    Path(_resolve_out(path)).unlink(missing_ok=True)
    _create(path, "0 FILE mf.ldr\n0 Minifigure\n0 Name: mf.ldr\n"
                  + block.group(1).strip() + "\n")
    told = json.loads(call_tool("validate_model", {"path": path, "grid_only": True}))
    figs = told.get("minifigures") or {}
    size = told.get("size") or {}
    print(f"   the template the prompt hands out -> passed={told.get('passed')}, "
          f"{figs.get('assembled')}/{figs.get('found')} assembled, "
          f"feet on the ground={size.get('ground_y') == 0}")
    assert told.get("passed"), \
        f"the minifigure the prompt tells the agent to copy does not validate: " \
        f"{told.get('verdict')}"
    assert figs.get("found") == 1 and figs.get("assembled") == 1, \
        "the prompt's minifigure is not recognised as one assembled figure"
    assert size.get("ground_y") == 0, \
        f"the prompt's minifigure does not stand on y=0 (ground_y="\
        f"{size.get('ground_y')}), so it will float or sink wherever it is put"

    # ...and the tool the same prompt tells it to put in that figure's hand.
    # A grip is nine numbers and a coordinate; nobody is going to spot a wrong
    # one by reading it.
    tool = next((b for b in re.findall(r"```\n(1 .*?)```", doc, re.S)
                 if "3847" in b), None)
    assert tool, "the minifigure prompt no longer shows a tool being held"
    Path(_resolve_out(path)).unlink(missing_ok=True)
    _create(path, "0 FILE mf.ldr\n0 Minifigure\n0 Name: mf.ldr\n"
                  + block.group(1).strip() + "\n" + tool.strip() + "\n")
    armed = json.loads(call_tool("validate_model", {"path": path, "grid_only": True}))
    arms = armed.get("minifigures") or {}
    grip = arms.get("held_accessories") or []
    print(f"   the tool the prompt puts in its hand -> passed={armed.get('passed')}, "
          f"held={[h['part'] for h in grip]}, "
          f"one piece={(armed.get('connectivity') or {}).get('subassemblies') == 1}")
    assert armed.get("passed"), \
        f"the prompt's worked example of a held tool fails: {armed.get('verdict')}"
    assert len(grip) == 1, \
        "the tool the prompt puts in a hand is not recognised as held — it " \
        "would show to the builder as a loose piece beside the figure"
    assert (armed.get("connectivity") or {}).get("subassemblies") == 1, \
        "a figure holding a tool does not read as one connected piece"

    # The poses. The claim the prompt makes about these is precise — the part
    # keeps its position and only its matrix changes — so a seated figure built
    # by following it must still read as one assembled figure. If posing broke
    # the check, every posed minifigure in every scene would fail validation.
    sitting = next((b for b in re.findall(r"```\n(1 .*?)```", doc, re.S)
                    if "3816.dat" in b and "3626" not in b), None)
    assert sitting, "the minifigure prompt no longer shows how to sit a figure"
    legs = {"3816.dat", "3817.dat"}
    upright = [ln for ln in block.group(1).strip().splitlines()
               if ln.split()[-1].lower() not in legs]
    Path(_resolve_out(path)).unlink(missing_ok=True)
    _create(path, "0 FILE mf.ldr\n0 Minifigure\n0 Name: mf.ldr\n"
                  + "\n".join(upright + sitting.strip().splitlines()) + "\n")
    seated = json.loads(call_tool("validate_model", {"path": path, "grid_only": True}))
    sat = seated.get("minifigures") or {}
    print(f"   the seated pose the prompt shows -> passed={seated.get('passed')}, "
          f"{sat.get('assembled')}/{sat.get('found')} assembled")
    assert seated.get("passed") and sat.get("assembled") == 1, \
        f"a figure posed the way the prompt says to no longer validates: " \
        f"{seated.get('verdict')}"

    for label, text in (("head floating above the neck", figure(head=-96)),
                        ("head sunk into the torso", figure(head=-36)),
                        ("legs off the hips", figure(legs=42))):
        Path(_resolve_out(path)).unlink(missing_ok=True)
        _create(path, text)
        bad = json.loads(call_tool("validate_model", {"path": path, "grid_only": True}))
        wrong = (bad.get("minifigures") or {}).get("misassembled_parts") or []
        print(f"   {label} -> caught: {not bad.get('passed')} "
              f"({len(wrong)} part(s): {wrong[0]['problem'] if wrong else '—'})")
        assert not bad.get("passed") and wrong, \
            f"a minifigure with its {label} validated clean"

    Path(_resolve_out(path)).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="Build LDraw models with an LLM agent.")
    ap.add_argument("task", nargs="?", help="What to build.")
    ap.add_argument("--task-file", help="Read the task from a file instead.")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"HF router model id. Default: {DEFAULT_MODEL}")
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--include-knowledge", action="store_true",
                    help="Append the full LDraw spec notes to the system prompt.")
    ap.add_argument("--out", default="cli_build",
                    help="Directory under out/ to build into. Default: cli_build")
    ap.add_argument("--flat", action="store_true",
                    help="Skip decomposition: one agent, one conversation, "
                         "the whole request as a single build.")
    ap.add_argument("--check-vision", action="store_true",
                    help="Probe the configured vision model and exit.")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--save-transcript", help="Write the tool transcript to this JSON file.")
    ap.add_argument("--self-test", action="store_true",
                    help="Exercise the tools. Needs the parts library; runs "
                         "without a token, though it will render and ask the "
                         "vision model if LeoCAD and a token are both there "
                         "(LDRAW_VISION=0 keeps it local).")
    ap.add_argument("--validate", metavar="PATH",
                    help="Validate an existing model and exit.")
    ap.add_argument("--show-prompt", action="store_true",
                    help="Print the assembled system prompt and exit.")
    ap.add_argument("--check-token", action="store_true",
                    help="Report where the HuggingFace token was found (never prints it).")
    args = ap.parse_args()

    if args.check_token:
        token, source = resolve_token()
        if not token:
            print("No HuggingFace token found. Set one of:")
            print("  export HF_TOKEN=hf_...")
            print(f"  echo 'HF_TOKEN=hf_...' > {ENV_FILE}")
            print("  huggingface-cli login")
            return 2
        print(f"Token found via: {source}")
        print(f"  length {len(token)}, starts with {token[:3]}...")
        return 0

    if args.check_vision:
        from . import render
        from .config import VISION_MODEL

        if not render.available():
            print("LeoCAD is not installed, so nothing can be rendered. "
                  "See simulator/README.md.")
            return 2
        print(f"LeoCAD: {render.leocad_binary()}")
        try:
            result = render.check_vision()
        except render.NotAvailable as exc:
            print(f"Vision: UNAVAILABLE\n  {exc}")
            return 2
        print(f"Vision: {result.get('vision_model') or VISION_MODEL} answered "
              f"— {result.get('reads_as') or result.get('critique', '')[:80]}")
        return 0

    if args.self_test:
        return self_test()

    if args.show_prompt:
        print(build_system_prompt(args.include_knowledge))
        return 0

    if args.validate:
        print(json.dumps(json.loads(call_tool("validate_model",
                                              {"path": args.validate})), indent=2))
        return 0

    task = args.task
    if args.task_file:
        task = open(args.task_file, encoding="utf-8").read()
    if not task:
        ap.error("give a task, --task-file, --self-test, --validate or --show-prompt")

    try:
        client = make_client()
    except MissingToken as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    llm = LLM(client=client, model=args.model, temperature=args.temperature,
              task="build")

    if args.flat:
        # One agent, one conversation, no decomposition — what this CLI did
        # before the harness existed. Kept for comparing the two.
        agent = LDrawAgent(llm=llm, max_steps=args.max_steps,
                           include_knowledge=args.include_knowledge,
                           verbose=not args.quiet,
                           state=RunState(subject=task, project=args.out))
        result = agent.run(task)
    else:
        orchestrator = Orchestrator(llm=llm, verbose=not args.quiet,
                                    max_steps=args.max_steps)
        result = orchestrator.run(task, project_dir=args.out)

    if args.save_transcript:
        with open(args.save_transcript, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nTranscript written to {args.save_transcript}")

    print("\n" + "=" * 70)
    print(result.get("answer") or "(no final message)")
    report = result.get("validation") or {}
    if report.get("verdict"):
        print(f"\n{report['verdict']}")
    if result.get("contact_sheet"):
        print(f"Renders: {result['contact_sheet']}")
    if result.get("warning"):
        print(f"\nWARNING: {result['warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
