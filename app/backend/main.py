#!/usr/bin/env python3
"""
Maister Builder - backend API.

Wraps the LDraw agent (maister/agent) and serves:
  * project CRUD over .ldr files
  * a chat endpoint that runs the agent in a worker thread, with polling so a
    slow reasoning model never hits an HTTP timeout
  * the LDraw parts library as static files, for three.js LDrawLoader in the
    browser

    conda activate hf_env
    python -m uvicorn app.backend.main:app --reload --port 8000
"""

import io
import json
import os
import re
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, File, HTTPException, UploadFile           # noqa: E402
from fastapi.middleware.cors import CORSMiddleware                     # noqa: E402
from fastapi.responses import FileResponse, PlainTextResponse, Response  # noqa: E402
from pydantic import BaseModel                                         # noqa: E402

from . import settings as app_settings                                    # noqa: E402
from maister import instructions                                          # noqa: E402
from maister.agent import (blueprint, catalog, connections, creations,  # noqa: E402
                           conversation, naming, planner, reference,
                           sets as setsdb, trace)
from maister.agent.agent import LDrawAgent                                # noqa: E402
from maister.agent import render                                          # noqa: E402
from maister.agent.config import (BLUEPRINT_MODEL, DEFAULT_MODEL,        # noqa: E402
                                  OUT_DIR, PARTS_DIR, PLANNER_ENABLED,
                                  VISION_ENABLED)
from maister.agent.library import ensure_library_root                     # noqa: E402
from maister.agent.llm import LLM, MissingToken, make_client              # noqa: E402
from maister.agent.orchestrator import Orchestrator                       # noqa: E402
from maister.agent.prompts import numbered_lines                          # noqa: E402
from maister.agent.tools import (agent_tools,                             # noqa: E402
                                 set_copy_from_set,
                                 _save_creation as tools_save_creation,
                                 _set_submodels as tools_set_submodels)
from maister.agent.validation import validate                             # noqa: E402

PROJECTS_DIR = OUT_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

BLANK_MODEL = """0 Untitled Model
0 Name: model.ldr
0 Author: Maister Builder
0 !LDRAW_ORG Model
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

0 // Empty project. Ask the agent to build something.
"""

app = FastAPI(title="Maister Builder")
# Any localhost origin on any port: Vite picks a different port when 5173 is
# taken, and "localhost" and "127.0.0.1" are distinct origins to the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

_agents = {}     # project_id -> LDrawAgent (the conversational path's history)
_runs = {}       # run_id -> dict
_lock = threading.Lock()

# How many past turns a build is told about. Enough for "make it bigger" to
# mean something; not so many that an old request competes with the new one.
HISTORY_TURNS = 6


def project_dir(project_id):
    d = PROJECTS_DIR / project_id
    if not d.is_dir():
        raise HTTPException(404, f"no project '{project_id}'")
    return d


def model_path(project_id):
    return project_dir(project_id) / "model.ldr"


def now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class CreateProject(BaseModel):
    name: str = "Untitled"
    content: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    colour: str | None = None


class ModelUpdate(BaseModel):
    content: str


class ChatMessage(BaseModel):
    message: str
    model: str | None = None
    # 0 means no limit, and is what the app sends: a run ends when the agent
    # finishes, gives up or is stopped. See DEFAULT_MAX_STEPS in config.py.
    max_steps: int = 0


class Messages(BaseModel):
    messages: list[dict] = []


class SettingsUpdate(BaseModel):
    model: str | None = None
    provider: str | None = None
    vision_model: str | None = None
    vision_provider: str | None = None
    # Whether the agent may graft assemblies out of released sets. None means
    # "leave it as it is", the same as every other field here.
    copy_from_set: bool | None = None


# --------------------------------------------------------------------------
# Settings
#
# Which model the agent runs on and which provider serves it. Stored on the
# backend (see settings.py) because the backend is what runs the agent.
# --------------------------------------------------------------------------

# BLUEPRINT_MODEL only inherits DEFAULT_MODEL when nothing set it; when the two
# differ, someone chose a separate planning model on purpose and a change made
# here is not an instruction to throw that away.
_BLUEPRINT_MODEL_IS_OWN = BLUEPRINT_MODEL != DEFAULT_MODEL


def _apply_model(model_id):
    """Point everything that talks to the router at `model_id`.

    Agents are cached per project and hold the conversation so far, so they are
    retargeted rather than dropped - changing the model should not silently end
    every open chat. The LLM is replaced wholesale instead of having its `model`
    reassigned: it carries flags negotiated against the previous model (whether
    it accepted tools, whether it would stream), and none of those transfer.
    """
    with _lock:
        agents = list(_agents.values())
    for agent in agents:
        try:
            agent.llm = LLM(client=agent.llm.client, model=model_id, task="build")
        except Exception:
            pass  # a client that cannot be reused is rebuilt on the next run
    if not _BLUEPRINT_MODEL_IS_OWN:
        blueprint.set_model(model_id)


def _apply_vision_model(model_id):
    """Point the render critic at `model_id`.

    Nothing to retarget and nothing cached: the critic builds its request per
    call, so this is a single module-level choice. Naming one here also turns
    off the fallback chain - a model someone chose is not a model to work
    around.

    Which is why the default is passed through as None instead. The settings
    file always holds a vision model, whether or not anybody ever opened the
    dialog, so pinning whatever it says would mean the app never has a fallback -
    and the chain exists precisely for the default nobody picked. A vision
    call that cannot be made costs the run its eyes, so the untouched default
    keeps its alternatives and only a real choice loses them.
    """
    default = app_settings.join_model(app_settings.DEFAULT_VISION_MODEL_NAME,
                                      app_settings.DEFAULT_VISION_PROVIDER)
    render.set_model(None if (model_id or "").strip() == default else model_id)


def _apply_copy_from_set(enabled):
    """Give or withhold `copy_from_set` for the whole process.

    One line, and no cached agent to chase - which is worth saying, because
    `_apply_model` above does have to chase them. The difference is where the
    tool list is decided: an agent is *handed* its LLM once and keeps it, but
    its tools are rebuilt from `tools.agent_tools()` every turn - by
    `_tools_for` on the chat path and by `_tools` on the build path. So the
    flag is read after this call, by everything, including the agents that were
    already open when the user changed it.
    """
    set_copy_from_set(enabled)


def _settings_payload():
    values = app_settings.load()
    return {
        **values,
        "effective_model": app_settings.join_model(values["model"], values["provider"]),
        "effective_vision_model": app_settings.join_model(
            values["vision_model"], values["vision_provider"]),
        # everything the dialog needs to render itself, so the list of known
        # providers lives in one place rather than two
        "policies": app_settings.POLICIES,
        "providers": app_settings.PROVIDERS,
        "suggested_models": app_settings.SUGGESTED_MODELS,
        "suggested_vision_models": app_settings.SUGGESTED_VISION_MODELS,
        "default_model": app_settings.DEFAULT_MODEL_NAME,
        "default_provider": app_settings.DEFAULT_PROVIDER,
        "default_vision_model": app_settings.DEFAULT_VISION_MODEL_NAME,
        "default_vision_provider": app_settings.DEFAULT_VISION_PROVIDER,
        "vision_enabled": VISION_ENABLED,
        "renderer_available": render.available(),
    }


@app.get("/api/settings")
def get_settings():
    return _settings_payload()


def _check_model(value, what):
    """Validate a model id from the settings dialog."""
    model = value.strip()
    if not model:
        raise HTTPException(400, f"the {what} id cannot be empty")
    if ":" in model:
        raise HTTPException(400, "put the provider in its own field, not "
                                 "after a colon")
    if len(model) > 200 or any(c.isspace() for c in model):
        raise HTTPException(400, f"that does not look like a {what} id")


def _check_provider(value):
    provider = value.strip()
    if provider and (len(provider) > 60 or not re.fullmatch(r"[\w.-]+", provider)):
        raise HTTPException(400, "that does not look like a provider")


@app.put("/api/settings")
def put_settings(body: SettingsUpdate):
    """Change either model, either provider, or any combination.

    Two models are configurable: the one that builds, and the one that looks at
    the renders and says whether the build resembles what was asked for. They
    are separate choices - the builder is text-only and the critic has to be
    multimodal, so one id could never serve both.

    Plus one switch: whether the agent may graft assemblies out of released
    sets. It is here rather than in an env var because it is a thing to change
    between one build and the next - grafting on to get a good model, off to
    find out what the agent designs by itself.

    No value is checked against a list. The router gains models and providers
    faster than any list here would be updated, so an unrecognised id is passed
    through and the router is left to be the one that says no.
    """
    if body.model is not None:
        _check_model(body.model, "model")
    if body.vision_model is not None:
        _check_model(body.vision_model, "vision model")
    if body.provider is not None:
        _check_provider(body.provider)
    if body.vision_provider is not None:
        _check_provider(body.vision_provider)

    values = app_settings.save(
        model=body.model, provider=body.provider,
        vision_model=body.vision_model, vision_provider=body.vision_provider,
        copy_from_set=body.copy_from_set)
    _apply_model(app_settings.join_model(values["model"], values["provider"]))
    _apply_vision_model(app_settings.join_model(values["vision_model"],
                                                values["vision_provider"]))
    _apply_copy_from_set(values["copy_from_set"])
    return _settings_payload()


# The choices outlive the process they were made in: pick them up on the way in.
_apply_model(app_settings.effective_model())
_apply_vision_model(app_settings.effective_vision_model())
_apply_copy_from_set(app_settings.copy_from_set())


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

@app.post("/api/projects")
def create_project(body: CreateProject):
    project_id = uuid.uuid4().hex[:12]
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True)
    (d / "model.ldr").write_text(body.content or BLANK_MODEL, encoding="utf-8")
    (d / "name.txt").write_text(body.name, encoding="utf-8")
    return {"id": project_id, "name": body.name, "created": now()}


def _read_meta(d, filename, default=None):
    path = d / filename
    if not path.is_file():
        return default
    return path.read_text(encoding="utf-8").strip() or default


def _project_record(d):
    return {
        "id": d.name,
        "name": _read_meta(d, "name.txt", d.name),
        "colour": _read_meta(d, "colour.txt"),
        "modified": datetime.fromtimestamp(d.stat().st_mtime, timezone.utc).isoformat(),
    }


@app.get("/api/projects")
def list_projects():
    return [_project_record(d)
            for d in sorted(PROJECTS_DIR.iterdir(),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if d.is_dir()]


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate):
    """Rename a project or recolour its brick. Both are cosmetic."""
    d = project_dir(project_id)

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "a project needs a name")
        (d / "name.txt").write_text(name[:120], encoding="utf-8")

    if body.colour is not None:
        colour = body.colour.strip()
        if colour and not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
            raise HTTPException(400, "colour must be a #rrggbb hex value")
        if colour:
            (d / "colour.txt").write_text(colour, encoding="utf-8")
        else:
            (d / "colour.txt").unlink(missing_ok=True)  # back to the derived colour

    return _project_record(d)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    import shutil
    # rmtree takes the reference/ directory with it: the images live inside the
    # project, which is the whole point of storing them there.
    # rmtree takes chat.json and the traces with it too - everything about a
    # project lives inside the project.
    shutil.rmtree(project_dir(project_id))
    with _lock:
        _agents.pop(project_id, None)
    return {"deleted": project_id}


@app.delete("/api/projects")
def delete_all_projects():
    """Erase every project. There is no undo, so the caller does the asking."""
    import shutil
    removed = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    with _lock:
        for project_id in removed:
            _agents.pop(project_id, None)
    return {"deleted": len(removed), "ids": removed}


def _archive_name(name, project_id, taken):
    """A filename for a project inside the zip: its own name where that works.

    Names are the user's and need not be unique or path-safe, so anything that
    would leave the archive's root is stripped and a collision falls back to the
    id, which is unique by construction.
    """
    stem = re.sub(r"[^\w \-.()]+", "_", (name or "").strip()).strip(". ") or project_id
    stem = stem[:80]
    if stem.lower() in taken:
        stem = f"{stem} ({project_id})"
    taken.add(stem.lower())
    return f"{stem}.ldr"


@app.get("/api/projects/archive.zip")
def download_all_projects():
    """Every project as one zip of .ldr files, plus a manifest.

    Built in memory: these are text files a few hundred lines long, and the
    alternative is a temporary file to clean up afterwards.
    """
    projects = [d for d in sorted(PROJECTS_DIR.iterdir(),
                                  key=lambda p: p.stat().st_mtime, reverse=True)
                if d.is_dir()]
    if not projects:
        raise HTTPException(404, "there are no projects to download")

    buffer = io.BytesIO()
    manifest = []
    taken = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for d in projects:
            record = _project_record(d)
            model = d / "model.ldr"
            if not model.is_file():
                continue
            filename = _archive_name(record["name"], record["id"], taken)
            archive.writestr(filename, model.read_text(encoding="utf-8", errors="replace"))
            manifest.append({**record, "file": filename})
        # so a name that had to be sanitised is still recoverable afterwards
        archive.writestr("projects.json", json.dumps(manifest, indent=2))

    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="maister-projects-{stamp}.zip"',
        },
    )


@app.post("/api/projects/upload")
async def upload_project(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 8_000_000:
        raise HTTPException(413, "file too large (max 8 MB)")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
    if not any(ln.strip().startswith(("0 ", "1 ")) for ln in content.splitlines()):
        raise HTTPException(400, "this does not look like an LDraw file")
    name = Path(file.filename or "uploaded.ldr").stem
    return create_project(CreateProject(name=name, content=content))


# --------------------------------------------------------------------------
# The agent's own library
#
# Models the agent built and chose to keep, via save_creation. Read-only here
# apart from delete: opening one copies it into a project, so editing a
# creation never mutates the library entry behind it.
# --------------------------------------------------------------------------

def _palette(path, limit=6):
    """Colour fingerprint of a model: LDraw colour codes by how often they are
    used. Enough to draw a recognisable swatch without rendering anything."""
    counts = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) > 14 and fields[0] == "1" and fields[14].lower().endswith(".dat"):
            counts[fields[1]] = counts.get(fields[1], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"colour": c, "count": n} for c, n in ranked[:limit]]


@app.get("/api/creations")
def list_creations():
    out = []
    for record in creations.load_creations():
        path = creations.model_path(record)
        summary = creations.summarize(record)
        summary["updated_at"] = record.get("updated_at")
        summary["verdict"] = record.get("verdict")
        summary["missing"] = not path.is_file()
        summary["palette"] = _palette(path) if path.is_file() else []
        out.append(summary)
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


@app.get("/api/creations/{creation_id}/model", response_class=PlainTextResponse)
def get_creation_model(creation_id: str):
    record = creations.resolve(creation_id)
    if record is None:
        raise HTTPException(404, f"no creation named {creation_id!r}")
    path = creations.model_path(record)
    if not path.is_file():
        raise HTTPException(404, "the model file for this creation is missing")
    return path.read_text(encoding="utf-8", errors="replace")


# Served as a real file so three.js LDrawLoader can fetch it by URL, exactly as
# it does for a project model.
@app.get("/api/creations/{creation_id}/model.ldr")
def get_creation_model_file(creation_id: str):
    record = creations.resolve(creation_id)
    if record is None:
        raise HTTPException(404, f"no creation named {creation_id!r}")
    path = creations.model_path(record)
    if not path.is_file():
        raise HTTPException(404, "the model file for this creation is missing")
    return FileResponse(path, media_type="text/plain")


@app.post("/api/creations/{creation_id}/open")
def open_creation(creation_id: str):
    """Copy a creation into a fresh project so it can be edited safely."""
    record = creations.resolve(creation_id)
    if record is None:
        raise HTTPException(404, f"no creation named {creation_id!r}")
    path = creations.model_path(record)
    if not path.is_file():
        raise HTTPException(404, "the model file for this creation is missing")
    return create_project(CreateProject(
        name=record.get("name") or "creation",
        content=path.read_text(encoding="utf-8", errors="replace"),
    ))


@app.delete("/api/creations/{creation_id}")
def delete_creation(creation_id: str):
    record = creations.delete(creation_id)
    if record is None:
        raise HTTPException(404, f"no creation named {creation_id!r}")
    return {"deleted": record.get("creation_id"), "name": record.get("name")}


# --------------------------------------------------------------------------
# The parts catalogue
#
# The other library: not what the agent built but what it builds out of. Every
# part in data/parts/parts_catalog.csv, with what the catalogue knows about it -
# what it is called, how big it is in studs and in millimetres, which categories
# it belongs to, how many real sets have used it.
# --------------------------------------------------------------------------

def _warm_stud_counts():
    """Count the studs on every part, once, off the request path.

    Whether a part has studs on top is read from its geometry rather than
    guessed from its bounding box, which means opening a file per part. Doing
    that catalogue-wide takes a few seconds - nothing at startup, where there is
    no one waiting, and a stall long enough to look broken if it happens under
    the first click of the filter instead.
    """
    def work():
        try:
            catalog.browse(has_studs=True, limit=1)
        except Exception:
            pass  # the filter falls back to doing it on demand
    threading.Thread(target=work, daemon=True).start()


_warm_stud_counts()


# Ahead of the /{part_id} route below, which would otherwise swallow it.
@app.get("/api/parts/categories")
def part_categories():
    return catalog.categories()


@app.get("/api/parts/connections")
def part_connection_families():
    """The connection vocabulary: systems, motions, and the families in each.

    Three levels, because a builder arrives with whichever one they have -
    the family ("a turntable"), the system it belongs to ("something Technic"),
    or only the behaviour they need ("something that spins").
    """
    return {
        "groups": [
            {"id": gid, "name": name, "description": blurb,
             "families": [fid for fid in connections.FAMILY_IDS
                          if connections.GROUP_OF[fid] == gid]}
            for gid, name, blurb in connections.GROUPS
        ],
        "motions": [
            {"id": mid, "name": name, "description": blurb,
             "families": [fid for fid in connections.FAMILY_IDS
                          if connections.MOTION_OF[fid] == mid]}
            for mid, name, blurb in connections.MOTIONS
        ],
        "families": [
            {"id": fid, "name": name, "group": group, "motion": motion,
             "does": connections.MOTION_LABELS[motion], "description": blurb}
            for fid, name, group, motion, blurb in connections.FAMILIES
        ],
    }


@app.get("/api/parts")
def browse_parts(query: str = "", category: str | None = None,
                 kind: str | None = None, width_studs: int | None = None,
                 depth_studs: int | None = None, has_studs: bool | None = None,
                 connection: str | None = None,
                 sort: str = "relevance", include_retired: bool = False,
                 limit: int = 48, offset: int = 0):
    """Search the catalogue. Every filter is optional and they all combine."""
    if sort not in catalog.SORTS:
        raise HTTPException(400, f"sort must be one of {', '.join(catalog.SORTS)}")
    for value, what in ((width_studs, "width_studs"), (depth_studs, "depth_studs")):
        if value is not None and not 1 <= value <= 48:
            raise HTTPException(400, f"{what} must be between 1 and 48")
    known = (set(connections.FAMILY_IDS) | set(connections.GROUP_IDS)
             | set(connections.MOTION_IDS))
    if connection and connection not in known:
        raise HTTPException(400, "unknown connection, system or motion")
    return catalog.browse(
        query=query, category=category, kind=kind, width_studs=width_studs,
        depth_studs=depth_studs, has_studs=has_studs, connection=connection,
        sort=sort, include_retired=include_retired,
        limit=max(1, min(int(limit), 200)), offset=max(0, int(offset)))


_PART_ID = re.compile(r"[\w.-]{1,64}")


@app.get("/api/parts/{part_id}")
def part_details(part_id: str):
    if not _PART_ID.fullmatch(part_id):
        raise HTTPException(400, "that is not a part number")
    for row in catalog.load_catalog():
        if (row.get("part_id") or "").lower() == part_id.lower():
            return catalog.describe(row)
    raise HTTPException(404, f"no part numbered {part_id!r} in the catalogue")


# A part on its own is a .dat with no colour of its own, and LDrawLoader wants
# a model. This is that model: one line, one part, one colour - which is what
# lets the gallery draw a part with exactly the pipeline that draws a build.
@app.get("/api/parts/{part_id}/model.ldr", response_class=PlainTextResponse)
def part_model(part_id: str, colour: int = 4):
    if not _PART_ID.fullmatch(part_id):
        raise HTTPException(400, "that is not a part number")
    name = part_id[:-4] if part_id.lower().endswith(".dat") else part_id
    if not (PARTS_DIR / f"{name}.dat").is_file():
        raise HTTPException(404, f"{name}.dat is not in the parts library")
    return (f"0 {name}\n0 Name: {name}.ldr\n"
            f"1 {int(colour)} 0 0 0 1 0 0 0 1 0 0 0 1 {name}.dat\n")


# --------------------------------------------------------------------------
# The set shelf
#
# 1,800 official models sit in data/ldraw_omr_sets, and until now the only way
# to see one was to ask the agent for it. They are the best material in the
# project - real designs, real coordinates - and a person browsing them finds
# things a semantic search never surfaces, so they get the same treatment the
# parts catalogue gets: a gallery, filters, and a way in.
#
# Rendering happens in the browser, from the LDraw source, by the same
# three.js renderer that draws the part swatches. Nothing is rendered here and
# nothing is cached on disk: a set is a few hundred lines of text, and the one
# thing the backend would add is a second copy of it going stale.
# --------------------------------------------------------------------------

_SET_NUMBER = re.compile(r"[\w.-]{1,32}")


@app.get("/api/sets")
def browse_sets(query: str = "", theme: str | None = None,
                min_pieces: int | None = None, max_pieces: int | None = None,
                year_min: int | None = None, year_max: int | None = None,
                sort: str = "name", limit: int = 48, offset: int = 0):
    """Search the official-set corpus. Every filter is optional."""
    if sort not in setsdb.BROWSE_SORTS:
        raise HTTPException(
            400, f"sort must be one of {', '.join(setsdb.BROWSE_SORTS)}")
    return setsdb.browse(query=query, theme=theme, min_pieces=min_pieces,
                         max_pieces=max_pieces, year_min=year_min,
                         year_max=year_max, sort=sort, limit=limit,
                         offset=offset)


@app.get("/api/sets/themes")
def set_themes():
    return {"themes": setsdb.themes()}


@app.get("/api/sets/{number}")
def set_details(number: str):
    """One set: what it is, and what it is assembled out of."""
    if not _SET_NUMBER.fullmatch(number):
        raise HTTPException(400, "that is not a set number")
    rows = setsdb.resolve(number)
    if not rows:
        raise HTTPException(404, f"no official model for set '{number}'")

    row = rows[0]
    detail = dict(setsdb.summarize(row))
    detail["source_url"] = row.get("source_url")
    try:
        detail["submodels"] = tools_set_submodels(row)
    except Exception:
        detail["submodels"] = []
    try:
        detail["top_parts"] = setsdb.top_parts(row, 16)
    except Exception:
        detail["top_parts"] = []
    return detail


@app.get("/api/sets/{number}/model.ldr", response_class=PlainTextResponse)
def set_model(number: str):
    """The set's LDraw source, for the browser to render."""
    if not _SET_NUMBER.fullmatch(number):
        raise HTTPException(400, "that is not a set number")
    rows = setsdb.resolve(number)
    if not rows:
        raise HTTPException(404, f"no official model for set '{number}'")
    path = setsdb.model_path(rows[0])
    if not path.is_file():
        raise HTTPException(404, f"the model file for {number} is missing")
    return path.read_text(encoding="utf-8", errors="replace")


class ProjectFromSet(BaseModel):
    name: str | None = None


@app.post("/api/sets/{number}/open")
def project_from_set(number: str, body: ProjectFromSet | None = None):
    """Start a new project with this set as its starting point.

    A copy, always. The corpus is reference and stays read-only - what lands in
    the project is the set's own source under the project's own name, which the
    user is then free to take apart.
    """
    if not _SET_NUMBER.fullmatch(number):
        raise HTTPException(400, "that is not a set number")
    rows = setsdb.resolve(number)
    if not rows:
        raise HTTPException(404, f"no official model for set '{number}'")

    row = rows[0]
    path = setsdb.model_path(row)
    if not path.is_file():
        raise HTTPException(404, f"the model file for {number} is missing")

    source = path.read_text(encoding="utf-8", errors="replace")
    project_id = uuid.uuid4().hex[:12]
    directory = PROJECTS_DIR / project_id
    directory.mkdir(parents=True)
    (directory / "model.ldr").write_text(source, encoding="utf-8")

    name = ((body.name if body else None)
            or f"{row.get('set_name') or number} (from {row.get('set_number')})")
    (directory / "name.txt").write_text(name, encoding="utf-8")
    return {"id": project_id, "name": name, "created": now(),
            "from_set": row.get("set_number")}


# --------------------------------------------------------------------------
# Reference images
#
# A picture the user attached to the project: what they want the model to look
# like. It belongs to the project rather than to the message that carried it -
# "make it taller" three turns later still means "and still like the picture".
# --------------------------------------------------------------------------

@app.get("/api/projects/{project_id}/references")
def list_references(project_id: str):
    project_dir(project_id)  # 404s if the project is unknown
    # ensure_edges rather than load: a picture attached before the chip
    # colours were kept has none recorded, and this is where they get measured
    # and written back. One pass per old project, then it is a plain read.
    return [reference.summarize(r) for r in reference.ensure_edges(project_id)]


@app.post("/api/projects/{project_id}/references")
async def add_reference(project_id: str, file: UploadFile = File(...)):
    """Attach an image, from the file picker or a clipboard paste."""
    project_dir(project_id)
    raw = await file.read()
    try:
        record = reference.add(project_id, raw,
                               content_type=file.content_type,
                               filename=file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return reference.summarize(record)


@app.get("/api/projects/{project_id}/references/{image_id}")
def get_reference(project_id: str, image_id: str):
    project_dir(project_id)
    record = reference.resolve(project_id, image_id)
    if record is None:
        raise HTTPException(404, "no such reference image")
    path = reference.image_path(record, project_id)
    if path is None:
        raise HTTPException(404, "the reference image is missing on disk")
    # `Vary: Origin` on a response that is cached for a day and served to two
    # different kinds of request. The <img> that shows the picture sends no
    # Origin and gets no CORS headers back; anything fetched from JS sends one
    # and does. Without this the browser may hand either cached copy to the
    # other, and the copy with no `Access-Control-Allow-Origin` fails the CORS
    # check of whatever asked for it. CORSMiddleware sets the header itself on
    # the responses it decorates - which is exactly the half of the pair that
    # did not need it.
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400",
                                       "Vary": "Origin"})


@app.delete("/api/projects/{project_id}/references/{image_id}")
def delete_reference(project_id: str, image_id: str):
    project_dir(project_id)
    record = reference.delete(project_id, image_id)
    if record is None:
        raise HTTPException(404, "no such reference image")
    return {"deleted": record.get("image_id")}


# --------------------------------------------------------------------------
# Model file
# --------------------------------------------------------------------------

def _project_files(project_id):
    """Every LDraw file this project holds: the scene, then its components.

    A scene is not built into ``model.ldr``. Each subconstruction is built into
    its own file under ``parts/`` and they are composed at the end, so while
    the build is running - and they run at the same time now - the model file
    is still the blank template and everything that is actually being written
    is somewhere the Source view could not see.
    """
    d = project_dir(project_id)
    found = []
    for path in [d / "model.ldr", *sorted((d / "parts").glob("*.ldr"))]:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.append({
            "file": "model.ldr" if path.parent == d else f"parts/{path.name}",
            "name": path.stem,
            "lines": len(text.splitlines()),
            "parts": sum(1 for ln in text.splitlines() if ln.startswith("1 ")),
            "modified": path.stat().st_mtime,
            "is_scene": path.parent == d,
        })
    return found


def _project_file(project_id, name):
    """One of this project's files, resolved safely under its own directory."""
    d = project_dir(project_id).resolve()
    target = (d / (name or "model.ldr")).resolve()
    if not str(target).startswith(str(d) + os.sep) or target.suffix != ".ldr":
        raise HTTPException(400, "that is not a file of this project")
    if not target.is_file():
        raise HTTPException(404, f"no such file: {name}")
    return target


@app.get("/api/projects/{project_id}/files")
def list_project_files(project_id: str):
    """The scene and every subconstruction being built into it."""
    model_path(project_id)  # 404s if the project is unknown
    return {"project": project_id, "files": _project_files(project_id)}


# Named by query rather than by path: a subconstruction is `parts/tree.ldr`,
# and a path converter greedy enough to hold that slash is also greedy enough
# to swallow anything meant to come after it.
@app.get("/api/projects/{project_id}/file", response_class=PlainTextResponse)
def read_project_file(project_id: str, name: str = "model.ldr"):
    """One file of this project, as text - for the editor and the viewer both."""
    return _project_file(project_id, name).read_text(encoding="utf-8",
                                                     errors="replace")


@app.get("/api/projects/{project_id}/model", response_class=PlainTextResponse)
def get_model(project_id: str):
    return model_path(project_id).read_text(encoding="utf-8")


# Served as a real file so three.js LDrawLoader can fetch it by URL.
@app.get("/api/projects/{project_id}/model.ldr")
def get_model_file(project_id: str):
    return FileResponse(model_path(project_id), media_type="text/plain",
                        headers={"Cache-Control": "no-store"})


@app.put("/api/projects/{project_id}/model")
def put_model(project_id: str, body: ModelUpdate):
    model_path(project_id).write_text(body.content, encoding="utf-8")
    return {"ok": True, "bytes": len(body.content)}


@app.post("/api/projects/{project_id}/validate")
def validate_project(project_id: str):
    # `index=True` wherever a report is on its way to the browser: the rail puts
    # each count back onto the model when you hover it, and it needs every part
    # behind a count rather than the sample the agent is shown. See
    # validation._part_index.
    return validate(model_path(project_id), index=True)


_instruction_locks = {}


@app.post("/api/projects/{project_id}/instructions")
def build_instructions(project_id: str):
    """The model as a building-instruction booklet, page-per-step, as a PDF.

    Rendered on demand rather than kept: it takes seconds, and a booklet is
    only ever wanted for the model as it stands right now.
    """
    d = project_dir(project_id)
    text = model_path(project_id).read_text(encoding="utf-8")
    if not any(line.lstrip().startswith("1 ") for line in text.splitlines()):
        raise HTTPException(400, "there are no parts in this model yet - build "
                                 "something first")

    name = _read_meta(d, "name.txt", d.name)
    # One booklet per project, rebuilt in place: two clicks in a row must not
    # have LPub3D writing over its own render cache from two directions.
    with _named_lock(_instruction_locks, project_id):
        target = OUT_DIR / "instructions" / project_id
        try:
            pdf = instructions.build(text, name=name, work_dir=target)
        except instructions.NotAvailable as e:
            raise HTTPException(503, str(e)) from None
        except Exception as e:
            raise HTTPException(500, f"could not build the instructions: {e}") from None

    return FileResponse(pdf, media_type="application/pdf",
                        filename=f"{pdf.stem} instructions.pdf")


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

def _get_agent(project_id, model_name):
    with _lock:
        agent = _agents.get(project_id)
        if agent is None:
            # the request may name a model for this run only; otherwise the one
            # chosen in Settings, which falls back to DEFAULT_MODEL
            llm = LLM(client=make_client(),
                      model=model_name or app_settings.effective_model(),
                      task="build")
            agent = LDrawAgent(llm=llm, verbose=False)
            # A restarted server builds this agent with an empty head. The
            # conversation on disk is what it said last time, so it is put back
            # - otherwise the transcript on screen shows an exchange the model
            # itself was never part of, and "make it taller" has no referent.
            try:
                agent.messages += conversation.as_messages(
                    project_id, limit=HISTORY_TURNS * 2)
            except Exception:
                pass
            _agents[project_id] = agent
        return agent


def _named_lock(registry, key):
    """One lock per key, created on first use."""
    with _lock:
        return registry.setdefault(key, threading.Lock())


def _remember(project_id, role, text, **extra):
    """Add one turn to a project's conversation, on disk.

    The build path runs a fresh sub-agent per subconstruction, so none of them
    carries a conversation of its own. This is the conversation - and it is
    also the transcript the browser reads, so the two cannot disagree.
    """
    text = (text or "").strip()
    if not text and not extra:
        return
    try:
        conversation.append(project_id, {"role": role, "text": text, **extra})
    except Exception:
        pass  # a conversation that cannot be written must not fail the run


def _record_reply(project_id, run):
    """Write this run's answer into the conversation, once.

    Carries the run id and the handful of events the transcript draws, so a
    reply reloaded tomorrow shows the same tool rows it showed live - and the
    run id is the way from a message to its full trace.
    """
    if run.get("recorded"):
        return
    run["recorded"] = True

    if run.get("status") == "error":
        _remember(project_id, "error", run.get("error") or "the run failed",
                  run_id=run.get("id"))
        return

    with _lock:
        events = [e for e in run.get("events") or []
                  if e.get("type") in conversation.DISPLAY_EVENTS]
    _remember(project_id, "assistant", run.get("answer") or "",
              run_id=run.get("id"), steps=run.get("steps"),
              warning=run.get("warning"), renamed=run.get("renamed"),
              events=events)


def _history_text(project_id):
    return conversation.history_text(project_id, limit=HISTORY_TURNS * 2)


_agent_locks = {}
# How long a new run waits for a stopped one to unwind. A stopped run returns
# at its next chunk or tool boundary, so this is generous; it exists so a run
# that will not unwind fails loudly instead of hanging the chat.
HANDOVER_TIMEOUT = 45


def _agent_lock(project_id):
    """One run at a time per project.

    Stop settles the run and returns at once, but the worker thread behind it
    is still inside the agent for a moment afterwards. The agent is cached per
    project and its message history is a single mutable list, so a new run
    starting in that moment would interleave with the old one and corrupt the
    conversation for both.
    """
    return _named_lock(_agent_locks, project_id)


def _blank_partial():
    return {"step": 0, "text": "", "tools": {}}


def _push(run, event):
    """Append one live event to a run. Events are only ever added, so the
    frontend can poll with ?since=<n> and receive just what is new.

    Streamed content is the exception: one event per token would be thousands
    of events for a single reply. It accumulates into ``run["partial"]``
    instead, which every poll returns in full and which is cleared as soon as
    a real event lands - by then the same text has arrived as a `text` event.
    """
    event = dict(event)

    # Written down whole, before anything below reduces it to a line of prose.
    # The trace is the only place a tool's actual arguments and actual answer
    # survive the run, which is the whole reason it exists.
    recorder = run.get("trace")
    if recorder is not None:
        recorder.event(event)

    # The agent's standing prompt: tens of KB, and the chat panel has no use
    # for it. It goes to the trace and no further - a live event stream that
    # carried it would re-send it on every poll.
    if event.get("type") == "context":
        return

    if event.get("type") in ("delta", "tool_stream"):
        with _lock:
            partial = run.setdefault("partial", _blank_partial())
            if partial["step"] != event.get("step"):
                partial.update(_blank_partial(), step=event.get("step") or 0)
            if event["type"] == "delta":
                partial["text"] += event.get("text") or ""
            else:
                # keyed by index so a call that arrives in pieces updates in place
                partial["tools"][str(event.get("index", 0))] = {
                    "tool": event.get("tool") or "",
                    "arguments": event.get("arguments") or "",
                }
        return

    with _lock:
        run["partial"] = _blank_partial()

    if event.get("type") == "tool_start":
        event["detail"] = _tool_detail(event["tool"], event.pop("arguments", ""))
    elif event.get("type") == "tool_end":
        event["summary"] = _tool_summary(event["tool"], event.pop("result", ""))
    with _lock:
        event["i"] = len(run["events"])
        event["at"] = now()
        run["events"].append(event)


def _tool_detail(tool, arguments):
    """One line describing what a tool call was asked to do."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return _clip(str(arguments), 80)
    if tool == "search_parts":
        bits = [str(args.get("query", ""))]
        if args.get("category"):
            bits.append(f"in {args['category']}")
        if args.get("width_studs") and args.get("depth_studs"):
            bits.append(f"{args['width_studs']}x{args['depth_studs']}")
        return " ".join(b for b in bits if b)
    if tool == "get_part_details":
        return str(args.get("part_id", ""))
    if tool == "search_reference":
        kind = str(args.get("kind") or "")
        bits = [kind, str(args.get("like") or args.get("query") or "")]
        if args.get("theme"):
            bits.append(f"in {args['theme']}")
        if args.get("subject_id"):
            bits.append(f"on {args['subject_id']}")
        if args.get("max_pieces"):
            bits.append(f"under {args['max_pieces']} pieces")
        return _clip(" ".join(b for b in bits if b), 80)
    if tool == "read_model":
        source = str(args.get("source", ""))
        if args.get("submodel"):
            source = f"{source} · {args['submodel']}"
        return _clip(source, 60)
    if tool == "get_set_details":
        return str(args.get("set_number", ""))
    if tool == "save_creation":
        return str(args.get("name", ""))
    if tool == "ask_about_image":
        asked = [q for q in (args.get("questions") or []) if q]
        if not asked:
            return _clip(str(args.get("request") or "describe it"), 80)
        more = f" +{len(asked) - 1} more" if len(asked) > 1 else ""
        return _clip(f"{_clip(str(asked[0]), 60)}{more}", 90)
    if tool == "add_note":
        subject = args.get("subject_type", "")
        if args.get("subject_id"):
            subject = f"{subject}:{args['subject_id']}"
        return _clip(f"{subject} - {args.get('text', '')}", 90)
    if tool in ("validate_model", "edit_model"):
        path = str(args.get("path", ""))
        if tool == "edit_model":
            edits = args.get("edits") or []
            where = ", ".join(
                str(e.get("start_line")) for e in edits[:3]
                if isinstance(e, dict) and e.get("start_line") is not None)
            more = "…" if len(edits) > 3 else ""
            return _clip(f"{Path(path).name} · {len(edits)} edit"
                         f"{'' if len(edits) == 1 else 's'}"
                         + (f" at line {where}{more}" if where else ""), 80)
        return Path(path).name
    return _clip(", ".join(f"{k}={v}" for k, v in args.items()), 80)


def _tool_summary(tool, result):
    """One line describing what a tool call gave back."""
    try:
        data = json.loads(result) if isinstance(result, str) else (result or {})
    except ValueError:
        return _clip(str(result), 80)
    if not isinstance(data, dict):
        return _clip(str(data), 80)
    if "error" in data:
        return _clip(str(data["error"]), 120)
    if tool == "search_parts":
        n = len(data.get("results") or [])
        return f"{n} part{'' if n == 1 else 's'} found"
    if tool == "get_part_details":
        desc = data.get("description") or data.get("dat_name") or "part"
        w, d = data.get("width_studs"), data.get("depth_studs")
        size = f" · {w}x{d} studs" if w and d else ""
        return _clip(f"{desc}{size}", 80)
    if tool == "search_reference":
        hits = data.get("results") or []
        if not hits:
            return "nothing found"
        top = hits[0]
        more = f" +{len(hits) - 1} more" if len(hits) > 1 else ""
        # one branch per corpus, told apart by what a hit carries
        if top.get("set_name"):
            return _clip(f"{top['set_name']} ({top.get('year') or '?'}){more}", 80)
        if top.get("name"):
            return _clip(f"{top['name']}{more}", 80)
        return _clip(f"{len(hits)} note{'' if len(hits) == 1 else 's'} · "
                     f"{top.get('text')}", 90)
    if tool == "get_set_details":
        blocks = len(data.get("submodels") or [])
        return _clip(f"{data.get('set_name')} · {data.get('total_lines', '?')} "
                     f"lines · {blocks} submodel{'' if blocks == 1 else 's'}", 80)
    if tool == "read_model":
        total = data.get("total_lines", "?")
        what = data.get("set_name") or data.get("name") or data.get("path")
        if data.get("submodel"):
            what = f"{what} · {data['submodel']}"
        shown = data.get("shown_lines")
        if shown:
            return _clip(f"{what} · lines {shown[0]}-{shown[1]} of {total}", 80)
        return _clip(f"{what} · {total} lines", 80)
    if tool == "save_creation":
        saved = data.get("saved") or {}
        mark = "validated" if saved.get("validated") else "NOT validated"
        return _clip(f"saved '{saved.get('name')}' · {saved.get('total_pieces')} pieces · {mark}", 80)
    if tool == "add_note":
        saved = data.get("saved") or {}
        return _clip(f"noted on {saved.get('subject')}: {saved.get('text')}", 90)
    if tool == "edit_model":
        # A create comes back through the same tool; it has no edits to count.
        if data.get("written"):
            return f"{data.get('lines', '?')} lines · {data.get('part_references', '?')} parts"
        applied = data.get("applied") or []
        added = sum(e.get("added", 0) for e in applied)
        removed = sum(e.get("removed", 0) for e in applied)
        return (f"{len(applied)} edit{'' if len(applied) == 1 else 's'} · "
                f"+{added}/-{removed} lines · now {data.get('lines', '?')} lines")
    if tool == "ask_about_image":
        answers = data.get("answers")
        if answers is None:
            seen = data.get("description") or data.get("summary")
            return _clip(str(seen or "looked at the picture"), 90)
        if isinstance(answers, list):
            n = len(answers)
            return _clip(f"{n} answer{'' if n == 1 else 's'}"
                         + (" (already known)" if data.get("cached") else "")
                         + (f" · {answers[0].get('answer')}"
                            if answers and isinstance(answers[0], dict) else ""), 90)
        return _clip(str(answers or "answered"), 90)
    if tool == "validate_model":
        # what it moved by itself is the part of a validation the user most
        # wants to see, so it leads the line whenever there was any
        mended = (data.get("auto_fixed") or {}).get("moved") or 0
        nudged = f"{mended} nudged back on grid · " if mended else ""
        if not data.get("passed"):
            misaligned = (data.get("connectivity") or {}).get("misaligned", "?")
            return f"{nudged}{misaligned} misaligned"
        grid = f"{nudged}passed · {data.get('parts', '?')} parts on grid"
        # the other half: this call looks at the model too, and what the
        # vision model made of it is the more interesting line of the two
        seen = data.get("seen") or {}
        reads = seen.get("reads_as")
        if reads:
            issues = len(seen.get("issues") or [])
            trouble = f" · {issues} issue{'' if issues == 1 else 's'}" if issues else ""
            return _clip(f"{grid} · looks like {reads}{trouble}", 110)
        return grid
    return _clip(json.dumps(data, default=str), 80)


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


# "Save this to my gallery" is a request to press a button, not to build
# anything, so it must not go through the build harness. The agent has no save
# tool any more - the button does it - but the phrasing still has to be routed
# away from decomposition.
_SAVE_REQUEST = re.compile(
    r"\b(save|keep|store|archive|bookmark)\b.{0,40}\b(it|this|that|model|build|creation|gallery|library)\b"
    r"|\b(add|put)\b.{0,30}\b(gallery|library|collection)\b",
    re.IGNORECASE | re.DOTALL,
)


# Never offered to a chat turn. `assemble_model` composes finished
# subconstruction files, which the harness does by itself, and the two
# arranging tools only mean anything inside a scene it has already built.
_WITHHELD = {"assemble_model", "move_submodel", "rotate_submodel"}


def _tools_for(message):
    return [t for t in agent_tools() if t["function"]["name"] not in _WITHHELD]


def _is_build(message):
    """Whether this turn should go through the build harness.

    The harness is what reads the workbench before it touches anything, splits
    the request into free-standing objects, and holds each one to a checklist.
    The conversational path has none of that - so what this decides is not
    "chat or build" but "surveyed and split, or neither".

    It used to require a build verb, and that was the wrong test: `a house`,
    `a tree and a car`, `MAISTER in big letters` are all requests for a model
    and none of them contains one. They went down the conversational path and
    were built, badly, by a single agent that had never been told what was
    already on the bench.

    So the question is now the other way round - see `planner.wants_model`.
    Everything is a build except the two things that certainly are not: a
    question about the model, and "save this", which is a request to press a
    button.
    """
    if _SAVE_REQUEST.search(message or ""):
        return False
    return planner.wants_model(message)


def _settle(run, **fields):
    """Record how a run ended, unless it has already been settled.

    Stop settles a run from the request thread and leaves the worker to unwind
    in its own time. Whatever the worker concludes afterwards - an answer, a
    step count, an exception raised on the way out - is about a run the user
    was already told was over, so it is dropped rather than allowed to
    resurrect it.
    """
    with _lock:
        if run.get("settled"):
            return
        run.update(fields)
        run["settled"] = True


# How long the settled run waits for a name that is not back yet. The call is
# started alongside the build and takes a second or two, so this is only ever
# reached when the provider is having a bad minute - and a name is never worth
# making someone wait for.
NAMING_GRACE = 3.0


# The two tools that are the first to know what is actually being built. The
# builder calls ask_about_image before it plans anything when a reference
# picture is attached, and plan_construction before it writes anything when
# there is not one - so whichever of the two comes back first is the earliest
# moment the project can be called something better than the request was.
NAMING_TRIGGERS = ("ask_about_image", "plan_construction")


def _naming_context(tool, result):
    """What a trigger tool learned, phrased for the namer. None if nothing."""
    try:
        data = json.loads(result) if isinstance(result, str) else (result or {})
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data:
        return None

    if tool == "decomposed":
        # What the run decided the request actually is, and what it split into.
        # Both matter to a title: "a lumberjack beside a pine tree" is a better
        # name than either object on its own.
        summary = " ".join(str(data.get("summary") or "").split())
        objects = [str(s.get("subject") or s.get("name") or "").strip()
                   for s in (data.get("subconstructions") or [])
                   if isinstance(s, dict)]
        objects = [o for o in objects if o]
        if not summary and not objects:
            return None
        said = summary or ", ".join(objects)
        if objects and len(objects) > 1:
            said = f"{said} - it is being built as: {', '.join(objects[:6])}"
        return f"What the request was understood to be: {said[:600]}"

    if tool == "ask_about_image":
        # only the describing half says what the picture is; the answering half
        # returns answers to questions and names nothing
        described = data.get("description")
        if described is None:
            return None
        if isinstance(described, dict):
            bits = [described.get("subject"), described.get("one_line")]
            whole = described.get("whole")
            if isinstance(whole, dict):
                bits.append(whole.get("dominant_colours"))
            said = " - ".join(str(b).strip() for b in bits if b)
        else:
            said = str(described or "")
        return (f"The reference picture they attached shows: {said}"
                if said.strip() else None)

    plan = data.get("plan")
    goal = plan.get("goal") if isinstance(plan, dict) else None
    subject = data.get("subject")
    said = " - ".join(str(b).strip() for b in (subject, goal) if b)
    return f"The construction plan is for: {said}" if said.strip() else None


def _start_naming(project_id, message, llm, context=None):
    """Begin naming an untitled project, in parallel with the build.

    Started the moment the run works out what it is building rather than up
    front, because up front all there is to go on is the request - and "build
    this" beside a photograph names nothing at all. It still runs on its own
    thread alongside the build, which is what keeps it off the clock the user
    is watching. Returns None when there is nothing to name: a project the user
    has already titled, or a request that is a question rather than a build.
    """
    d = project_dir(project_id)
    if not naming.needs_title(_read_meta(d, "name.txt", d.name)):
        return None
    # A trigger tool having run is itself proof this is a build; the guess from
    # the wording is only needed when nothing else has said so.
    if context is None and not planner.needs_plan(message):
        return None

    holder = {}

    def work():
        # Its own LLM over the shared HTTP client: the builder's is in use on
        # another thread, and the two must not trade capability flags.
        try:
            # task="chat" even though it borrows the builder's model: naming a
            # project places no bricks and is not worth a thinking block.
            holder["title"] = naming.title_for(
                message, context=context,
                llm=LLM(client=llm.client, model=llm.model, task="chat"))
        except Exception:
            holder["title"] = None

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, holder


def _watch_for_name(run, project_id, llm, event):
    """Name the project as soon as the run knows what it is building.

    Hung off the event stream rather than called from the build: the builder
    and the orchestrator both run these tools, at a depth neither of them
    reports upward, and the events are the one place both are already visible.
    Fires at most once - the first trigger wins, and it is the earliest.
    """
    if run.get("namer") is not None:
        return

    # The split is the first moment anything knows what is being built - it is
    # the very first pass of a run, before a brief is written or a brick is
    # placed, and it produces the one-line summary of the request. Naming from
    # it means the project is titled while the build is still starting, rather
    # than several minutes in when the builder happens to plan or read a
    # picture. Those stay as triggers below for a run that never splits.
    if event.get("type") == "decomposed":
        context = _naming_context("decomposed", event)
    elif event.get("type") == "tool_end":
        if event.get("tool") not in NAMING_TRIGGERS or not event.get("ok"):
            return
        context = _naming_context(event.get("tool"), event.get("result"))
    else:
        return
    if not context:
        return
    try:
        namer = _start_naming(project_id, run.get("message") or "", llm,
                              context=context)
    except Exception:
        return
    if namer is not None:
        run["namer"] = namer


def _built_context(result):
    """What a finished run says it made, for naming a build that never planned."""
    said = " ".join(str(result.get("answer") or "").split())
    return f"What was built: {said[:600]}" if said else None


def _apply_name(namer, project_id):
    """Write the name that was being worked out, if it arrived in time."""
    if namer is None:
        return None
    thread, holder = namer
    thread.join(timeout=NAMING_GRACE)

    title = holder.get("title")
    if not title:
        return None

    # The user may have named it themselves while the build ran; theirs wins.
    d = project_dir(project_id)
    if not naming.needs_title(_read_meta(d, "name.txt", d.name)):
        return None
    (d / "name.txt").write_text(title[:120], encoding="utf-8")
    return title


def _run_agent(run_id, project_id, message, model_name, max_steps):
    run = _runs[run_id]
    agent = None
    before = None

    handover = _agent_lock(project_id)
    if not handover.acquire(timeout=HANDOVER_TIMEOUT):
        _settle(run, status="error",
                error="the previous run has not finished yet - give it a "
                      "moment, or start a new conversation")
        run["finished"] = now()
        return

    try:
        agent = _get_agent(project_id, model_name)
        agent.max_steps = max_steps
        agent.tools = _tools_for(message)
        agent.should_stop = lambda: run.get("stop", False)  # noqa: E731

        # Every event goes to the watcher and to the namer. The namer is
        # waiting for the run to describe the picture or draw up the plan,
        # which is the first moment there is something worth naming.
        def watched(event):
            _push(run, event)
            _watch_for_name(run, project_id, agent.llm, event)

        agent.on_event = watched

        current = before = model_path(project_id).read_text(encoding="utf-8")

        # Turn a one-line request into a brief with real coordinates before the
        # builder starts. Questions go through untouched.
        stop = lambda: run.get("stop", False)  # noqa: E731

        brief = None
        if PLANNER_ENABLED and planner.needs_plan(message) and not stop():
            _push(run, {"type": "planning"})
            brief = planner.plan(
                message, current, agent.llm,
                on_delta=lambda piece: _push(run, {"type": "delta", "step": 0, "text": piece}),
                should_stop=stop,
            )
            if brief:
                _push(run, {"type": "plan", "text": brief})

        # Stopped while planning: never start the build.
        if stop():
            _settle(run, answer="", steps=0, stopped=True,
                    warning="stopped before building started",
                    model_changed=False,
                    validation=validate(model_path(project_id), index=True),
                    status="stopped")
            return

        rel = f"projects/{project_id}/model.ldr"

        if _is_build(message):
            # The build path. The request is split into atomic
            # subconstructions, each built by its own agent against its own
            # gate, then assembled. See maister/agent/orchestrator.py.
            orchestrator = Orchestrator(llm=agent.llm, verbose=False,
                                        on_event=watched)
            orchestrator.should_stop = stop
            result = orchestrator.run(
                message,
                project_dir=f"projects/{project_id}",
                current_model=current,
                project=project_id,
                history=_history_text(project_id),
            )
        else:
            # The conversational path: a question, a comment, "save this".
            # One agent, its own history, no decomposition and no gate - there
            # is nothing here to finish.
            task = (
                f"The current project file is `{rel}` (relative to the out/ "
                f"directory, which is what edit_model and "
                f"validate_model expect).\n\n"
                f"Its current contents are, with line numbers - those are the "
                f"numbers `edit_model` takes, so change this model by editing "
                f"the lines that change rather than by writing the file "
                f"out again:\n```\n{numbered_lines(current)}\n```\n\n"
                f"User request: {message}"
            )
            result = agent.run(planner.apply_plan(task, brief))

        after = model_path(project_id).read_text(encoding="utf-8")

        # An untitled project that now holds a build has something to be named
        # after. Skipped when the run was stopped: the user wanted it to end,
        # not to spend another call.
        renamed = None
        if after != before and not result.get("stopped") and not stop():
            try:
                namer = run.get("namer")
                if namer is None:
                    # Nothing described the picture and nothing planned - a
                    # small edit, usually. There is still a finished model to
                    # name it after, so name it from that. Started here rather
                    # than in parallel because this case is rare and is almost
                    # always a project that already has a name.
                    namer = _start_naming(project_id, message, agent.llm,
                                          context=_built_context(result))
                renamed = _apply_name(namer, project_id)
            except Exception:
                renamed = None
            if renamed:
                _push(run, {"type": "renamed", "text": renamed})

        _settle(run,
                renamed=renamed,
                answer=result.get("answer") or "",
                warning=result.get("warning"),
                steps=result.get("steps"),
                stopped=bool(result.get("stopped")),
                model_changed=after != before,
                validation=validate(model_path(project_id), index=True),
                status="stopped" if result.get("stopped") else "done")
    except MissingToken as e:
        _settle(run, status="error", error=str(e))
        _push(run, {"type": "error", "text": str(e)})
    except Exception as e:
        _settle(run, status="error", error=f"{type(e).__name__}: {e}")
        _push(run, {"type": "error", "text": f"{type(e).__name__}: {e}"})
    finally:
        # A run that crashed or was stopped may still have written the model,
        # and a half-built model is the thing the user most wants to look at.
        # Report the file as it stands whatever happened to the run - this one
        # is recorded even on a settled run, because it is about the file on
        # disk rather than about the run's outcome.
        if before is not None:
            try:
                after = model_path(project_id).read_text(encoding="utf-8")
                with _lock:
                    run["model_changed"] = after != before
                    run["validation"] = validate(model_path(project_id), index=True)
            except Exception:
                pass
        if agent is not None:
            # The agent is cached per project and outlives this run. Both hooks
            # close over `run`, and a stale should_stop pointing at a stopped
            # run would abort the *next* one the moment it started.
            agent.on_event = None
            agent.should_stop = None
        run["finished"] = now()

        # The reply, recorded here rather than on the happy path: a run that
        # crashed, or that the user stopped, still said something, and a
        # transcript missing every reply but the clean ones is a transcript
        # that disagrees with what happened.
        try:
            _record_reply(project_id, run)
        except Exception:
            pass

        # Seal the trace with how it ended. Everything that happened is already
        # on disk - this is the outcome, which is only known here.
        recorder = run.get("trace")
        if recorder is not None:
            try:
                recorder.close(**{k: v for k, v in run.items()
                                  if k not in _PRIVATE_RUN_KEYS})
            except Exception:
                pass
        handover.release()


@app.post("/api/projects/{project_id}/chat")
def chat(project_id: str, body: ChatMessage):
    model_path(project_id)  # 404s if the project is unknown
    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {"id": run_id, "status": "running", "started": now(),
                     # Which project this belongs to, so a page that has
                     # forgotten the run can find it again - see
                     # `active_run`. Without it a reload left a build running
                     # with no way to watch it and no way to stop it.
                     "project": project_id,
                     "events": [], "answer": None,
                     # what was asked, for the namer, which is started from
                     # inside the run rather than alongside it
                     "message": body.message,
                     # the run's own record on disk, which outlives both this
                     # dict and the process holding it
                     "trace": trace.Recorder(project_id, run_id, body.message),
                     "partial": _blank_partial()}
    # Recorded now, not inside the worker: what was asked is known here, and a
    # request that never reaches a reply is still part of the conversation.
    try:
        attached = [reference.summarize(r) for r in reference.load(project_id)]
    except Exception:
        attached = []
    _remember(project_id, "user", body.message, run_id=run_id,
              images=attached or None)
    threading.Thread(
        target=_run_agent,
        args=(run_id, project_id, body.message, body.model, body.max_steps),
        daemon=True,
    ).start()
    return {"run_id": run_id, "status": "running"}


@app.get("/api/projects/{project_id}/run")
def active_run(project_id: str):
    """The run still in flight for this project, if there is one.

    The composer keeps the run it started in React state, and React state does
    not survive a reload, a switch to another project and back, or a second tab.
    The build survives all three - it is a thread on the backend - so without
    this the user is left watching a model change under a Send button, with the
    Stop button gone and no way to ask for it back.

    Returns the run the page should re-attach to, or ``{"run_id": None}``.
    """
    model_path(project_id)  # 404s if the project is unknown
    with _lock:
        live = [r for r in _runs.values()
                if r.get("project") == project_id and r.get("status") == "running"]
    if not live:
        return {"run_id": None}
    # Newest, on the off-chance two ever overlap: it is the one whose events
    # are still arriving.
    run = max(live, key=lambda r: r.get("started") or "")
    return {"run_id": run["id"], "status": run["status"],
            "started": run.get("started"), "message": run.get("message")}


@app.post("/api/projects/{project_id}/chat/reset")
def reset_chat(project_id: str):
    """Start a new conversation: the record goes, and the agent holding it too."""
    model_path(project_id)  # 404s if the project is unknown
    conversation.clear(project_id)
    with _lock:
        _agents.pop(project_id, None)
    return {"ok": True, "project": project_id}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    """Stop a run, now.

    The flag alone was not enough. It is only read between chunks, tool calls
    and steps, so a Stop pressed while the model was composing its first token
    or while a slow tool was in flight did nothing visible for as long as that
    took - which reads as a button that does not work.

    So the run is settled here instead: it is marked stopped immediately and
    reported that way on the very next poll, and the worker thread is left to
    notice the flag and unwind on its own. Anything it says afterwards is
    ignored (see ``_settled``), because the user has already been told the run
    is over. Whatever it wrote to the model file still counts, and is picked up
    in ``_run_agent``'s finally.
    """
    run = _runs.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    with _lock:
        run["stop"] = True
        if run.get("status") == "running":
            run["settled"] = True
            run["status"] = "stopped"
            run["stopped"] = True
            # what the agent had written by the time the user gave up on it
            run["answer"] = (run.get("partial") or {}).get("text", "").strip()
            run["warning"] = "stopped on request - the model may be half-built"
            run["finished"] = now()
    return {"ok": True, "run": run_id, "status": run.get("status")}


# Keys a run carries for its own bookkeeping, which never go over the wire.
# `namer` is the naming thread and its holder - a Thread has a lock inside it,
# and handing one to the JSON encoder fails the whole response. `trace` is the
# open recorder, and holds a lock for the same reason.
_PRIVATE_RUN_KEYS = {"events", "namer", "trace"}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str, since: int = 0):
    """Run state plus every event recorded after index `since`."""
    run = _runs.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    with _lock:
        total = len(run["events"])
        events = run["events"][max(0, since):]
        rest = {k: v for k, v in run.items() if k not in _PRIVATE_RUN_KEYS}
    return {**rest, "events": events, "next_since": total}


def _describe_creation(project_id, name, target):
    """Write the library entry for a model, from everything the project knows.

    The description is what the creations search matches on, so "a tree" makes
    a model unfindable the moment there are three of them. This hands the whole
    conversation, the name the project ended up with and the file's own
    arithmetic to the model and asks for a catalogue entry.

    Best effort throughout: a save that works is worth more than a save that
    waited for prose and then failed.
    """
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
        report = validate(target)
        counts = {}
        for line in text.splitlines():
            bits = line.split()
            if len(bits) >= 15 and bits[0] == "1":
                counts[bits[14]] = counts.get(bits[14], 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        facts = (f"{report.get('parts')} parts, "
                 f"{len(counts)} distinct; most used: "
                 + ", ".join(f"{p} x{n}" for p, n in top)
                 + f"; validation: {report.get('verdict')}")
        return naming.description_for(
            title=name,
            conversation=conversation.history_text(project_id, limit=40),
            facts=facts,
            model=text,
            llm=LLM(client=make_client(),
                    model=app_settings.effective_model(), task="chat"),
        ) or ""
    except Exception:
        return ""


class SaveCreation(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] = []


@app.post("/api/projects/{project_id}/save")
def save_to_gallery(project_id: str, body: SaveCreation):
    """Put this project's model in the gallery.

    Gated on the grid check and nothing else. A model that does not sit on the
    stud grid is not a model anybody can build, so it is refused; whether it
    *looks* like anything is a judgement for the person pressing the button,
    and making them wait on a vision call to find out would be the wrong trade.
    """
    target = model_path(project_id)
    if not target.is_file():
        raise HTTPException(404, "this project has no model yet")

    d = project_dir(project_id)
    name = (body.name or "").strip() or _read_meta(d, "name.txt", d.name)
    description = (body.description or "").strip() or _describe_creation(
        project_id, name, target)

    result = tools_save_creation(
        str(target.relative_to(OUT_DIR)), name, description,
        tags=body.tags, require_valid=True)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# --------------------------------------------------------------------------
# The conversation - one record, on disk, read by every window
# --------------------------------------------------------------------------

@app.get("/api/projects/{project_id}/messages")
def get_messages(project_id: str):
    """This project's transcript. The browser reads; the server writes."""
    model_path(project_id)  # 404s if the project is unknown
    return {"project": project_id, "messages": conversation.load(project_id)}


@app.put("/api/projects/{project_id}/messages")
def put_messages(project_id: str, body: Messages):
    """Replace the transcript.

    Only for carrying a conversation over from the browser storage this used
    to live in, once, the first time a project is opened after the upgrade.
    Nothing else writes this way - two windows doing so is exactly the race
    that lost people their conversations in the first place.
    """
    model_path(project_id)
    return {"project": project_id,
            "messages": conversation.replace(project_id, body.messages)}


# --------------------------------------------------------------------------
# Traces - what the agent did, kept after the run that did it
# --------------------------------------------------------------------------

@app.get("/api/projects/{project_id}/traces")
def project_traces(project_id: str):
    """Every recorded run of this project, newest first."""
    model_path(project_id)  # 404s if the project is unknown
    return {"project": project_id, "runs": trace.runs(project_id)}


# Registered before the run route below, which would otherwise swallow it:
# "images" is a perfectly good run id as far as the path converter is concerned.
@app.get("/api/projects/{project_id}/traces/images/{name}")
def trace_image(project_id: str, name: str):
    """One picture a run rendered or was shown, as it was at that moment.

    Content-addressed and never rewritten, so it can be cached hard - that is
    the point of keeping a copy rather than pointing at out/renders, where the
    next build overwrites the file this trace is talking about.
    """
    model_path(project_id)
    path = trace.image_path(project_id, name)
    if path is None:
        raise HTTPException(404, "no such trace image")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/projects/{project_id}/traces/{run_id}")
def project_trace(project_id: str, run_id: str):
    """One run as a graph: nodes, edges, and what went into and out of each.

    Readable while the run is still going - the events are appended to disk as
    they happen, so polling this gives a graph that grows.
    """
    model_path(project_id)
    graph = trace.graph(project_id, run_id)
    if graph is None:
        raise HTTPException(404, "no trace was recorded for that run")
    return graph


@app.delete("/api/projects/{project_id}/traces")
def clear_traces(project_id: str):
    """Throw away this project's history of runs. The models are untouched."""
    model_path(project_id)
    trace.forget(project_id)
    return {"ok": True, "project": project_id}


@app.get("/api/health")
def health():
    try:
        make_client()
        token = True
    except MissingToken:
        token = False
    return {"ok": True, "model": app_settings.effective_model(),
            "token_configured": token,
            "library": str(ensure_library_root() or "")}


# --------------------------------------------------------------------------
# Static: the LDraw parts library, for LDrawLoader in the browser
# --------------------------------------------------------------------------

_library = ensure_library_root()

# The library root uses symlinks into data/lego_pieces, and StaticFiles refuses
# to follow a symlink out of the directory it serves, so the real directory is
# served directly. data/lego_pieces is a *merged* library: parts, primitives and
# the s/, 48/ and 8/ subfolders all sit at the top level, with no parts/ or p/
# split. LDrawLoader assumes the official split layout and probes for a subfile
# in a fixed order - for "s\3003s01.dat" it rewrites the name to
# "parts/s/3003s01.dat" and then asks for
#
#   /ldraw/parts/parts/s/3003s01.dat   /ldraw/p/parts/s/3003s01.dat
#   /ldraw/models/parts/s/3003s01.dat  /ldraw/parts/s/3003s01.dat   <- hit
#
# The part does resolve, on the fourth try, so the model renders correctly; but
# the first three log as 404s that look like missing pieces and cost a round
# trip each. Collapsing the layout distinction here answers all four with the
# same file: every leading parts/ p/ models/ segment is stripped, and what is
# left is looked up flat in the merged library.
_LIBRARY_PREFIXES = ("parts", "p", "models")


# Registered before the catch-all below, which would otherwise swallow it:
# LDConfig.ldr lives in data/parts, not in the merged piece library.
@app.get("/ldraw/LDConfig.ldr")
@app.get("/ldraw/ldconfig.ldr")
def ldconfig():
    path = PROJECT_ROOT / "data" / "parts" / "LDConfig.ldr"
    if not path.is_file():
        raise HTTPException(404, "LDConfig.ldr not found")
    return FileResponse(path, media_type="text/plain")


@app.get("/ldraw/{path:path}")
def ldraw_file(path: str):
    if not PARTS_DIR.is_dir():
        raise HTTPException(404, "parts library not available")

    parts = [seg for seg in path.replace("\\", "/").split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in parts):
        raise HTTPException(400, "invalid path")
    while len(parts) > 1 and parts[0].lower() in _LIBRARY_PREFIXES:
        parts.pop(0)
    if not parts:
        raise HTTPException(404, "not found")

    target = PARTS_DIR.joinpath(*parts)
    if not target.is_file():
        # LDraw references are case-insensitive; the library on disk is not.
        try:
            lower = parts[-1].lower()
            for entry in target.parent.iterdir():
                if entry.name.lower() == lower and entry.is_file():
                    target = entry
                    break
            else:
                raise HTTPException(404, f"{path} not found in the parts library")
        except OSError:
            raise HTTPException(404, f"{path} not found in the parts library")

    # the library is read-only and content-addressed by name: safe to cache hard
    return FileResponse(target, media_type="text/plain",
                        headers={"Cache-Control": "public, max-age=86400"})
