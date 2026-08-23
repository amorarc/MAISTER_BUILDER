"""Every state a run passed through, written down so it can be read afterwards.

A run reports itself as it goes - a step began, a tool was called, a subbuild
ended - and the web UI polls those events to animate the chat panel. But they
live in a dict in the server's memory, they are stripped down to one line each
on the way out, and they are gone the moment the process restarts. So the one
question worth asking about a build afterwards - *why did it do that* - has
never been answerable. The arguments a tool was called with and the answer it
gave back, the prompt the builder was working from, which turn decided what:
all of it was thrown away as soon as it had been summarised.

This keeps it. Every event of every run is appended to a file under the
project, whole, with the arguments and results still attached, and
``graph()`` reads one back as a node-and-edge structure: the run at the root,
the phases under it, a node per *iteration* - every turn of the loop up to the
point the model was checked - a node per tool call, each one carrying what went
in and what came out, pictures included.

Three files and a folder per run, all under ``out/projects/<project>/traces/``:

* ``<run>.jsonl``        - the events, one JSON object per line, append-only
* ``<run>.meta.json``    - what the run was and how it ended
* ``<run>.prompts.json`` - system prompts, by hash
* ``images/``            - every picture the run rendered or was shown, shared
  by all of a project's runs and named by content hash

The prompts are kept apart and deduplicated because every sub-agent in a build
works from the same 30 KB of standing context, and a scene with eight
subconstructions in it would otherwise write that same text eight times.

Append-only is the point: a run that crashes, hangs or is killed still leaves
behind everything it did up to that moment, which is exactly the run someone
wants to look at.
"""

import hashlib
import io
import json
import shutil
import threading
import time
from pathlib import Path

from .config import OUT_DIR

PROJECTS_DIR = OUT_DIR / "projects"
TRACE_DIRNAME = "traces"
IMAGES_DIRNAME = "images"

# How many runs a project keeps. Past this the oldest are deleted: a trace is
# a few hundred KB, and nobody is going back forty builds.
MAX_RUNS = 24

# The longest single string stored. A tool that returns four hundred lines of
# LDraw is worth keeping; it is not worth keeping twice over in a file someone
# has to load into a browser.
MAX_FIELD = 60_000

# Events that exist only to drive the live UI. They arrive thousands at a time
# and say nothing a `text` event does not say afterwards.
SKIP = {"delta", "tool_stream"}

# How long a run may go without recording anything before a reader stops
# believing it is still running. A run is only ever settled by the thread that
# owns it, so one killed with the server - a restart, a crash, a reload during
# a build - leaves a trace that claims to be in progress for ever, and anything
# watching it waits for ever too.
#
# Half an hour, which is far longer than it sounds like it needs to be. This
# was five minutes, on the reasoning that "a single slow turn of the loop can
# be a minute of silence on its own" - and that was simply wrong. A builder
# writing a whole subconstruction does it in ONE turn, and it emits nothing
# between asking the model and getting the file back: a scene of two objects
# measured 352 seconds of silence in that turn, comfortably past the old
# threshold. What the reader did with that was worse than waiting would have
# been, because a run reported abandoned has every node still in flight marked
# "cut off" - so a build that was merely thinking hard showed up as a build
# that had died.
#
# The asymmetry is the whole argument. Waiting too long costs a stale trace
# saying "running" until someone looks again; giving up too early tells the
# user their build is dead while it is still working. Only the second one is a
# lie, so the threshold is set past the slowest turn anyone has seen rather
# than close to it. It stays derived at read time, so a run that was merely
# thinking says "running" again the moment it speaks.
STALE_AFTER = 1800.0


def _dir(project):
    return PROJECTS_DIR / str(project) / TRACE_DIRNAME


def _clip(value, limit=MAX_FIELD):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n\n… [{len(value) - limit:,} more characters]"
    return value


def _load(path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


# ---------------------------------------------------------------- writing --


class Recorder:
    """One run, being written down as it happens.

    Every method swallows its own IO errors and sets ``ok`` False. A disk that
    is full must cost the user their trace, not their build.
    """

    def __init__(self, project, run_id, message=""):
        self.project = str(project)
        self.run_id = str(run_id)
        self.dir = _dir(project)
        self.ok = True
        self._lock = threading.Lock()
        self._prompts = {}
        self._count = 0

        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._events_path().write_text("", encoding="utf-8")
        except OSError:
            self.ok = False

        self.meta = {
            "run_id": self.run_id,
            "project": self.project,
            "message": _clip(str(message or ""), 4000),
            "started": time.time(),
            "status": "running",
        }
        self._write_meta()

    # -- paths ------------------------------------------------------------
    def _events_path(self):
        return self.dir / f"{self.run_id}.jsonl"

    def _meta_path(self):
        return self.dir / f"{self.run_id}.meta.json"

    def _prompts_path(self):
        return self.dir / f"{self.run_id}.prompts.json"

    # -- writing ----------------------------------------------------------
    def _write_meta(self):
        if not self.ok:
            return
        try:
            self._meta_path().write_text(
                json.dumps(self.meta, ensure_ascii=False, indent=1, default=str),
                encoding="utf-8")
        except OSError:
            self.ok = False

    def _prompt(self, text):
        """Store a system prompt once, and answer with the hash to refer to it."""
        text = str(text or "")
        sha = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        if sha in self._prompts:
            return sha
        self._prompts[sha] = text
        try:
            self._prompts_path().write_text(
                json.dumps(self._prompts, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return sha

    def event(self, event):
        """Append one event, whole - arguments, results and all."""
        if not self.ok or not isinstance(event, dict):
            return
        if event.get("type") in SKIP:
            return

        event = dict(event)
        if "system" in event:
            event["system_sha"] = self._prompt(event.pop("system"))
        event = {k: _clip(v) for k, v in event.items()}
        event.setdefault("at", time.time())

        with self._lock:
            event["n"] = self._count
            self._count += 1
            line = json.dumps(event, ensure_ascii=False, default=str)
            try:
                with self._events_path().open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                self.ok = False

    # Keys the recorder owns and a settling run must not overwrite. The server
    # keeps its clocks as ISO strings; these are epoch seconds, and `runs()`
    # sorts on them - one string in there and the sort raises.
    OWNED = {"started", "finished", "events", "run_id", "project", "partial"}

    def close(self, **fields):
        """Record how the run ended, then drop the oldest traces."""
        self.meta.update({k: _clip(v, 8000) for k, v in fields.items()
                          if k not in self.OWNED})
        self.meta["finished"] = time.time()
        self.meta["events"] = self._count
        self._write_meta()
        prune(self.project)


def prune(project, keep=MAX_RUNS):
    """Delete all but the newest ``keep`` runs of a project."""
    metas = sorted(_dir(project).glob("*.meta.json"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True) if _dir(project).is_dir() else []
    for stale in metas[keep:]:
        run_id = stale.name[: -len(".meta.json")]
        for suffix in (".meta.json", ".jsonl", ".prompts.json"):
            try:
                (_dir(project) / f"{run_id}{suffix}").unlink(missing_ok=True)
            except OSError:
                pass


def forget(project):
    """Drop every trace a project has, pictures included."""
    directory = _dir(project)
    if not directory.is_dir():
        return
    for path in directory.glob("*"):
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------- pictures --
#
# A run's pictures, kept with the run that took them.
#
# Renders are written to stable filenames under out/renders so the app can
# point an <img> at a URL that does not change - which means the next write
# overwrites them. That is right for the viewer and wrong for a trace: reading
# a build back tomorrow would show today's model beside yesterday's decision,
# and the one question the trace exists to answer is what the agent was
# actually looking at when it decided that. So every picture a tool produces is
# copied here first.
#
# Copied as a thumbnail, and named after the hash of the original: a trace is
# evidence rather than artwork, and a model rendered twice without changing is
# then stored once.

THUMB_MAX = 480
THUMB_QUALITY = 80

# How many pictures a project's trace archive keeps. Past this the oldest go,
# the same way whole runs do - and for the same reason.
MAX_IMAGES = 600


def _images_dir(project):
    return _dir(project) / IMAGES_DIRNAME


def _thumbnail(raw, target):
    """Write ``raw`` image bytes out small, flattened onto white.

    Flattened because a render may carry an alpha channel, and JPEG has no
    room for one - left to itself the transparent background comes out black,
    which reads as a model photographed in a cave.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    flat = Image.new("RGB", image.size, "white")
    flat.paste(image, mask=image.split()[-1])
    flat.thumbnail((THUMB_MAX, THUMB_MAX))
    flat.save(target, "JPEG", quality=THUMB_QUALITY)


def _prune_images(directory, keep=MAX_IMAGES):
    try:
        files = sorted(directory.glob("*.jpg"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def keep_image(project, source, kind="render", view=None, label=None):
    """Archive one picture beside this project's traces.

    Returns the record to put in the event - ``{"src": <filename>, ...}`` -
    or None if the picture could not be read or written. Never raises: a build
    must not fail because a thumbnail did.
    """
    try:
        raw = Path(source).read_bytes()
    except OSError:
        return None

    name = hashlib.sha1(raw).hexdigest()[:16] + ".jpg"
    directory = _images_dir(project)
    target = directory / name
    if not target.is_file():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _thumbnail(raw, target)
        except Exception:
            return None
        _prune_images(directory)

    record = {"src": name, "kind": kind}
    if view:
        record["view"] = view
    if label:
        record["label"] = label
    return record


def image_path(project, name):
    """One archived picture, resolved safely inside the project's own folder."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = _images_dir(project) / name
    return path if path.is_file() else None


# ---------------------------------------------------------------- reading --


def _status_of(meta, directory, run_id):
    """A run's status, with one it never got to disown corrected.

    The recorder writes ``running`` up front and only the run's own thread ever
    replaces it. If that thread went down with the process, nothing ever will -
    so a run that claims to be running and has not written a line in five
    minutes is reported as what it is: abandoned, not in progress.
    """
    status = meta.get("status")
    if status != "running":
        return status
    try:
        quiet = time.time() - (directory / f"{run_id}.jsonl").stat().st_mtime
    except OSError:
        return status
    return "abandoned" if quiet > STALE_AFTER else status


def runs(project):
    """Every recorded run of a project, newest first, as summaries."""
    directory = _dir(project)
    if not directory.is_dir():
        return []

    found = []
    for path in directory.glob("*.meta.json"):
        meta = _load(path)
        if not isinstance(meta, dict):
            continue
        run_id = meta.get("run_id") or path.name[: -len(".meta.json")]
        found.append({
            "run_id": run_id,
            "message": meta.get("message"),
            "status": _status_of(meta, directory, run_id),
            "started": meta.get("started"),
            "finished": meta.get("finished"),
            "events": meta.get("events"),
            "steps": meta.get("steps"),
            "answer": _clip(str(meta.get("answer") or ""), 240),
            "model_changed": meta.get("model_changed"),
        })
    return sorted(found, key=lambda r: -(r.get("started") or 0))


def read(project, run_id):
    """``(meta, events, prompts)`` for one run, or ``(None, [], {})``."""
    directory = _dir(project)
    meta = _load(directory / f"{run_id}.meta.json")
    if not isinstance(meta, dict):
        return None, [], {}

    events = []
    try:
        with (directory / f"{run_id}.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue  # a torn last line, from a run killed mid-write
    except OSError:
        pass

    prompts = _load(directory / f"{run_id}.prompts.json", {}) or {}
    meta = {**meta, "status": _status_of(meta, directory, run_id)}
    return meta, events, prompts


# ------------------------------------------------------------ the graph ----

def _parse(value):
    """A tool's arguments or result, as data if it is JSON and text if not."""
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


# The calls that end an iteration.
#
# A turn of the loop is not a unit of work anybody wants to click on: the model
# reads the catalogue in one, writes the file in the next, and reads the result
# in a third, and none of the three is an attempt at anything. An *iteration*
# is - everything from the first turn up to the moment the build is checked or
# the run is ended. Those two calls are the only places the agent says "that
# attempt is over", so they are the only places a new node begins.
ITERATION_ENDS = {"validate_model", "finish"}


class _Builder:
    """Turns a run's event stream back into the shape the run actually had.

    The events are flat and interleaved - a scene builds eight subconstructions
    and every one of them emits `step` and `tool_start` under its own name. The
    lane is what puts them back where they belong: each event carries the
    subconstruction or phase it came from, and each lane keeps its own current
    iteration to hang tool calls off.
    """

    def __init__(self, meta, prompts):
        self.meta = meta
        self.prompts = prompts
        self.nodes = []
        self.edges = []
        self.by_id = {}

        # The latest moment anything was recorded. What a node that never got
        # an ending of its own is closed at.
        self.clock = meta.get("started") or 0.0

        # lane -> its design brief node, so the subconstruction that the brief
        # was written for hangs off it rather than beside it. The brief is a
        # step in the pipeline, not a thing that happened alongside the build:
        # it is decided first and the build is made from it.
        self.briefs = {}
        self.lanes = {}       # lane -> the node its iterations hang under
        self.step = {}        # lane -> the iteration now running
        self.iteration = {}   # lane -> how many iterations it has had
        self.calls = {}       # (lane, call_id) -> tool node

        self.root = self._node(
            "run", label=_first_line(meta.get("message")) or "run",
            title="The request", status=_status(meta.get("status")),
            at=meta.get("started"))
        self.root["input"] = {"user_request": meta.get("message")}

    # -- building blocks --------------------------------------------------
    def _node(self, kind, label, parent=None, **fields):
        # Depth is the column a node lands in, and it comes from whatever holds
        # the node rather than from its kind: the assembly phase calls tools
        # with no turn in between, and a fixed column per kind would leave that
        # tool floating one column clear of its own parent with nothing between.
        node = {
            "id": f"n{len(self.nodes)}",
            "kind": kind,
            "label": label,
            "depth": parent["depth"] + 1 if parent is not None else 0,
            "lane": None,
            "status": None,
            "input": None,
            "output": None,
            **fields,
        }
        self.nodes.append(node)
        self.by_id[node["id"]] = node
        if parent is not None:
            self.edges.append({"from": parent["id"], "to": node["id"],
                               "kind": "in"})
        return node

    def _close(self, node, when=None, ms=None):
        """Mark when a node finished, and how long it took to get there.

        Everything is timed, not just tool calls: the question a trace is read
        with is usually "what took so long", and until now the only thing that
        could answer it was the one node kind that happened to carry a
        stopwatch. A turn, a subconstruction, the assembly, the whole run -
        each knows when it started, so each can say how long it lasted.
        """
        if node is None or node.get("ended") is not None:
            return
        when = when if when is not None else self.clock
        node["ended"] = when
        if ms is not None:
            # Measured inside the agent, around the call itself - tighter than
            # the difference between two timestamps written either side of it.
            node["ms"] = ms
        elif node.get("at"):
            node["ms"] = max(0, int(round((when - node["at"]) * 1000)))

    def _sequence(self, before, after):
        if before is not None and after is not None:
            self.edges.append({"from": before["id"], "to": after["id"],
                               "kind": "next"})

    def _lane(self, event):
        return event.get("subconstruction") or event.get("phase") or "main"

    def _holder(self, event):
        """The node a turn from this event's lane belongs under."""
        return self.lanes.get(self._lane(event)) or self.root

    # -- the stream -------------------------------------------------------
    def feed(self, event):
        kind = event.get("type")
        lane = self._lane(event)
        at = event.get("at")
        if at:
            self.clock = max(self.clock, at)

        if kind == "context":
            # The standing prompt and the task, emitted by an agent as it
            # starts. It belongs to whatever is holding that agent - the
            # subbuild node, or the run itself for a plain chat turn.
            holder = self._holder(event)
            holder["context"] = {
                "system": self.prompts.get(event.get("system_sha") or ""),
                "task": event.get("task"),
                "tools": event.get("tools"),
                "max_steps": event.get("max_steps"),
            }
            if holder is self.root and holder["input"] is not None:
                holder["input"]["task_given_to_the_agent"] = event.get("task")

        elif kind == "reference":
            node = self._node("reference", "reference picture", self.root,
                              lane=lane, at=at, title="A picture to build from")
            node["output"] = event.get("image")
            if event.get("images"):
                node["images"] = event["images"]

        elif kind == "workbench":
            # What the run found in the model file before it decided anything.
            # First under the root, because it happened first and because every
            # decision after it was made knowing this.
            node = self._node("workbench", "read the workbench", self.root,
                              lane=lane, at=at, status="ok",
                              title="What was already built, before this run "
                                    "changed anything")
            node["output"] = {"summary": event.get("summary"),
                              "empty": event.get("empty"),
                              "survey": event.get("survey")}
            if event.get("images"):
                node["images"] = event["images"]
            self._close(node, at)

        elif kind == "planning":
            self._node("planning", "planning", self.root, lane=lane, at=at,
                       status="running", title="Turning the request into a brief")

        elif kind == "plan":
            node = self._find("planning") or self._node(
                "planning", "planning", self.root, lane=lane, at=at)
            node["output"] = {"brief": event.get("text")}
            node["status"] = "ok"
            self._close(node, at)

        elif kind == "decomposing":
            self._node("decompose", "decomposing", self.root, lane=lane, at=at,
                       status="running", title="Splitting the request into objects")

        elif kind == "decomposed":
            node = self._find("decompose") or self._node(
                "decompose", "decomposing", self.root, lane=lane, at=at)
            subs = event.get("subconstructions") or []
            node["output"] = {
                "summary": event.get("summary"),
                "scene": event.get("scene"),
                "source": event.get("source"),
                "subconstructions": subs,
            }
            node["status"] = "ok"
            node["label"] = f"{len(subs)} subconstruction" + ("" if len(subs) == 1 else "s")
            self._close(node, at)

        elif kind == "tool_retry":
            # Hung off whatever the lane is doing rather than opening a node of
            # its own: a retry is part of the call that failed, and shown as a
            # sibling it reads as a second, unexplained call.
            holder = self._holder(event)
            retries = holder.setdefault("retries", [])
            retries.append({"tool": event.get("tool"),
                            "attempt": event.get("attempt"),
                            "error": event.get("error"),
                            "arguments": event.get("arguments")})

        elif kind == "design_brief":
            # It belongs to the object it was written for, not to the run. The
            # brief is decided inside a subbuild - after that lane has opened -
            # so hanging it off the root left it floating beside the object it
            # describes, with nothing connecting the two. Under the subbuild it
            # is the first thing in that lane, which is also the order it
            # happened in.
            name = event.get("name") or lane
            holder = self.lanes.get(name) or self.root
            node = self._node("brief", "design brief", holder,
                              lane=name, at=at, status="ok",
                              title="What this object should look like, "
                                    "decided before it was planned")
            node["output"] = {"for": event.get("name"),
                              "brief": event.get("brief")}
            self._close(node, at)
            # Only worth remembering when it arrived before its subbuild did:
            # that is the case `subbuild_start` uses to hang the object under
            # its brief instead. Remembering it in the other case would make
            # the next subbuild in the same lane a child of this node.
            if holder is self.root:
                self.briefs[name] = node

        elif kind == "requirements":
            # The checklist this object is judged against, beside the brief
            # that shaped it. Both were decided for this object before a brick
            # was placed, and this is the one that decides when it stops: a
            # reader asking "why did this run keep going" is asking about this
            # node.
            name = event.get("name") or lane
            holder = self.lanes.get(name) or self.root
            record = event.get("record") or {}
            wanted = [r for r in (record.get("requirements") or [])
                      if isinstance(r, dict)]
            node = self._node("requirements", "requirements to finish", holder,
                              lane=name, at=at, status="ok",
                              title="What has to be true before this build is "
                                    "allowed to end")
            node["label"] = (f"{len(wanted)} requirement"
                             + ("" if len(wanted) == 1 else "s"))
            node["output"] = {
                "for": name,
                "reused": bool(event.get("reused")),
                "requirements": wanted,
                "rejected_as_unmeasurable":
                    record.get("rejected_as_unmeasurable") or [],
                "rejected_as_not_asked_for":
                    record.get("rejected_as_not_asked_for") or [],
            }
            self._close(node, at)

        elif kind == "requirements_checked":
            # One of these ends every iteration, so it hangs off the iteration
            # it judged rather than off the object - the question it answers is
            # "was *that* attempt enough", and shown as a sibling of the object
            # it would read as a verdict on the whole build.
            met = event.get("met") or []
            unmet = event.get("unmet") or []
            holder = self.step.get(lane) or self.lanes.get(lane) or self.root
            node = self._node("requirements_check", "requirements check",
                              holder, lane=lane, at=at,
                              status="ok" if event.get("passed") else "warn",
                              title="Every requirement put to the model that "
                                    "was just built, one at a time")
            node["label"] = (f"{len(met)}/{len(met) + len(unmet)} met")
            node["output"] = {
                "passed": bool(event.get("passed")),
                "summary": event.get("summary"),
                "met": met,
                "unmet": unmet,
            }
            self._close(node, at)

        elif kind == "reference_sets":
            # The real sets this object was handed before it started. Under the
            # subbuild, beside its brief: both are things decided for this
            # object before a brick was placed, and both are the answer to
            # "where did that come from" when the model turns out to look like
            # a set somebody owns.
            name = event.get("name") or lane
            holder = self.lanes.get(name) or self.root
            node = self._node("sets", "reference sets", holder,
                              lane=name, at=at, status="ok",
                              title="Real sets that already built this, opened "
                                    "and handed to the builder")
            node["output"] = {"for": name, "summary": event.get("summary"),
                              "sets": event.get("sets")}
            self._close(node, at)

        elif kind == "editing":
            self.root["editing"] = event.get("changes")

        elif kind == "subbuild_start":
            # Hung off this object's design brief when it has one, so the trace
            # reads brief -> build in the order the run actually went. With no
            # brief - an edit to an existing model - it hangs off the root as
            # it always did.
            name = event.get("name")
            holder = self.briefs.get(name) or self.root
            node = self._node("subbuild", name or "subbuild",
                              holder, lane=name, at=at,
                              status="running",
                              title=event.get("subject"),
                              index=event.get("index"), total=event.get("total"))
            node["input"] = {"name": event.get("name"),
                             "subject": event.get("subject"),
                             "is": f"{event.get('index')} of {event.get('total')}"}
            self.lanes[event.get("name") or lane] = node

        elif kind == "subbuild_end":
            self._settle_step(event.get("name") or lane, at)
            node = self.lanes.get(event.get("name"))
            if node is not None:
                node["status"] = "ok" if event.get("ok") else "error"
                node["output"] = {**(node.get("output") or {}),
                                  "ok": event.get("ok"),
                                  "note": event.get("note")}
                self._close(node, at)

        elif kind == "assembling":
            node = self._node("assembly", "assembling", self.root,
                              lane="assembly", at=at, status="running",
                              title="Composing the finished parts into one scene")
            node["input"] = {"components": event.get("components")}
            self.lanes["assembly"] = node

        elif kind == "assembled":
            node = self.lanes.get("assembly")
            if node is not None:
                node["status"] = "ok" if event.get("ok") else "error"
                node["output"] = {"ok": event.get("ok"),
                                  "verdict": event.get("verdict"),
                                  "components": event.get("components"),
                                  "note": event.get("note")}
                self._close(node, at)

        elif kind == "scene_seen":
            node = self._node("critique", "looked at the scene", self.root,
                              lane=lane, at=at, status="ok",
                              title="What the renders were judged to show")
            self._close(node, at)
            node["output"] = {"critique": event.get("critique"),
                              "contact_sheet": event.get("contact_sheet")}
            if event.get("images"):
                node["images"] = event["images"]

        elif kind == "step":
            holder = self._holder(event)
            previous = self.step.get(lane)

            # The loop came round again inside the same attempt - nothing has
            # been checked since the last node was opened, so this turn belongs
            # to it rather than starting one of its own.
            if previous is not None and not previous.get("_sealed"):
                previous["turns"] = previous.get("turns", 0) + 1
                previous["last_step"] = event.get("step")
                return

            # The iteration before this one is over: it ran, it called its
            # tools, and it got as far as checking the model. Only `answer`
            # used to settle a node, which left every one but the last reading
            # "cut off".
            self._settle_step(lane, at)
            index = self.iteration.get(lane, 0) + 1
            self.iteration[lane] = index
            node = self._node("step", f"Iteration {index}", holder,
                              lane=lane, at=at, status="running",
                              step=event.get("step"), iteration=index, turns=1,
                              last_step=event.get("step"),
                              title=f"Iteration {index} in {lane} - every turn "
                                    f"up to the next validate_model or finish")
            if previous is None:
                context = (holder.get("context") or {})
                node["input"] = {"kind": "task",
                                 "task": context.get("task"),
                                 "system_prompt": bool(context.get("system"))}
            else:
                node["input"] = {
                    "kind": "tool_results",
                    "from": [c["id"] for c in previous.get("_calls", [])],
                }
            node["_calls"] = []
            self._sequence(previous, node)
            self.step[lane] = node

        elif kind == "text":
            node = self.step.get(lane)
            if node is not None:
                # An iteration is several turns, and each of them may think out
                # loud. Kept as a list so the later ones do not erase the
                # earlier ones - reading them in order is reading the attempt.
                said = list((node.get("output") or {}).get("text") or [])
                said.append(event.get("text"))
                node["output"] = {**(node.get("output") or {}), "text": said}

        elif kind == "answer":
            node = self.step.get(lane)
            if node is not None:
                node["status"] = "ok"
                node["output"] = {**(node.get("output") or {}),
                                  "answer": event.get("text")}
                self._close(node, at)
            holder = self._holder(event)
            holder["output"] = {**(holder.get("output") or {}),
                                "answer": event.get("text")}

        elif kind == "tool_start":
            holder = self.step.get(lane) or self._holder(event)
            node = self._node("tool", event.get("tool") or "tool", holder,
                              lane=lane, at=at, status="running",
                              tool=event.get("tool"), step=event.get("step"),
                              title=event.get("tool"))
            node["input"] = _parse(event.get("arguments"))
            self.calls[(lane, event.get("call_id"))] = node
            if holder.get("kind") == "step":
                holder.setdefault("_calls", []).append(node)

        elif kind == "tool_end":
            node = self.calls.get((lane, event.get("call_id")))
            if node is None:
                node = self._node("tool", event.get("tool") or "tool",
                                  self.step.get(lane) or self._holder(event),
                                  lane=lane, at=at, tool=event.get("tool"))
            node["status"] = "ok" if event.get("ok") else "error"
            node["output"] = _parse(event.get("result"))
            if event.get("images"):
                node["images"] = event["images"]
            self._close(node, at, ms=event.get("ms"))
            # The model has been checked, or the run has been ended: whatever
            # comes next is a fresh attempt, not more of this one.
            if (event.get("tool") or "") in ITERATION_ENDS:
                holder = self.step.get(lane)
                if holder is not None:
                    holder["_sealed"] = True

        elif kind == "error":
            self._node("error", "error", self.root, lane=lane, at=at,
                       status="error", title=event.get("text"),
                       output={"error": event.get("text")})

        elif kind == "renamed":
            self.root["renamed"] = event.get("text")

        elif kind == "done":
            self.root["output"] = {**(self.root.get("output") or {}),
                                   "model": event.get("model"),
                                   "answer": event.get("answer"),
                                   "stopped": event.get("stopped")}

    def _settle_step(self, lane, when=None):
        """The iteration running in this lane got to the end of itself.

        An iteration that called a tool never emits `answer` - the loop just
        comes round again - so nothing else marks it finished, and it would be
        reported as abandoned by a run that in fact completed it.
        """
        node = self.step.get(lane)
        if node is not None and node["status"] == "running":
            node["status"] = "ok"
        self._close(node, when)

    def _find(self, kind):
        """The most recent node of a kind - for the events that come in pairs."""
        for node in reversed(self.nodes):
            if node["kind"] == kind:
                return node
        return None

    # -- the result -------------------------------------------------------
    def finish(self):
        # A run killed part-way leaves nodes still saying "running". They were
        # not running when the file was read; they were abandoned.
        settled = self.meta.get("status") not in (None, "running")
        for node in self.nodes:
            node.pop("_calls", None)
            node.pop("_sealed", None)
            if settled and node["status"] == "running":
                node["status"] = "cut off"

        self.root["output"] = {**(self.root.get("output") or {}),
                               "answer": self.meta.get("answer"),
                               "warning": self.meta.get("warning"),
                               "model_changed": self.meta.get("model_changed"),
                               "validation": self.meta.get("validation")}
        self.root["status"] = _status(self.meta.get("status"))

        # The run's own span, and then everything still open. A node with no
        # ending is one the run never got back to - it is closed at the last
        # thing that happened, which is the truthful answer to "how long was it
        # going for" even though it never finished.
        # The later of the two clocks: a run is not over before the last thing
        # it recorded, whatever its own bookkeeping says it finished at.
        ended = max(self.meta.get("finished") or 0.0, self.clock)
        self._close(self.root, ended or None)
        for node in self.nodes:
            self._close(node, self.clock)

        counts = {}
        for node in self.nodes:
            counts[node["kind"]] = counts.get(node["kind"], 0) + 1

        # Wall clock against the work inside it. Subconstructions are built in
        # parallel, so the second number is the larger one whenever that
        # actually happened - and their ratio is what the parallelism bought.
        subbuilds = [n for n in self.nodes if n["kind"] == "subbuild"]
        work = sum(n.get("ms") or 0 for n in subbuilds)
        wall = self.root.get("ms") or 0
        timing = {
            "wall_ms": wall,
            "subbuild_ms": work,
            "subbuilds": len(subbuilds),
            "slowest_ms": max((n.get("ms") or 0 for n in subbuilds), default=0),
            # >1 means objects really were being built at the same time
            "overlap": round(work / wall, 2) if wall and work else None,
        }

        return {
            "meta": {**self.meta, "counts": counts, "timing": timing},
            "nodes": self.nodes,
            "edges": self.edges,
        }


def _status(status):
    return {"done": "ok", "error": "error", "stopped": "cut off",
            "running": "running"}.get(status, status)


def _first_line(text, limit=70):
    line = " ".join(str(text or "").split())
    return line[:limit] + ("…" if len(line) > limit else "")


def graph(project, run_id):
    """One run as connected nodes: what happened, in what order, inside what."""
    meta, events, prompts = read(project, run_id)
    if meta is None:
        return None
    builder = _Builder(meta, prompts)
    for event in events:
        try:
            builder.feed(event)
        except Exception:
            # One malformed event must not cost the whole trace.
            continue
    return builder.finish()
