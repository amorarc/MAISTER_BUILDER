import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "./components/Composer";
import EmptyStage from "./components/EmptyStage";
import ErrorBoundary from "./components/ErrorBoundary";
import Gallery from "./components/Gallery";
import ImportDialog from "./components/ImportDialog";
import PartsGallery from "./components/PartsGallery";
import SetsGallery from "./components/SetsGallery";
import PartsUsed from "./components/PartsUsed";
import ProjectRail from "./components/ProjectRail";
import SettingsDialog from "./components/SettingsDialog";
import SourceEditor from "./components/SourceEditor";
import TopBar from "./components/TopBar";
import TraceView from "./components/TraceView";
import Viewer3D from "./components/Viewer3D";
import { api } from "./api";
import "./styles.css";

export default function App() {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(null);
  const [version, setVersion] = useState(0);
  const [validation, setValidation] = useState(null);
  const [source, setSource] = useState("");
  const [saved, setSaved] = useState("");
  const [view, setView] = useState("model");
  const [screen, setScreen] = useState("build"); // "build" | "gallery" | "parts" | "sets"
  const [health, setHealth] = useState(null);
  const [notice, setNotice] = useState(null);
  const [importing, setImporting] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [running, setRunning] = useState(false); // a build is in flight
  const [building, setBuilding] = useState(false); // instructions being rendered
  const [saving, setSaving] = useState(false);    // going into the gallery
  const [runId, setRunId] = useState(null); // the last run started, for Trace
  // The piece the caret is on in the source view, for the viewer to light up.
  const [cursorPart, setCursorPart] = useState(null);
  // Which build-check count the mouse is over in the rail, so the viewer can
  // paint the parts behind it. Null whenever the mouse is somewhere else.
  const [check, setCheck] = useState(null);
  // A scene is several files. `file` is the one on screen; `files` is what
  // there is to choose from, which grows as subconstructions are written.
  const [file, setFile] = useState("model.ldr");
  const [files, setFiles] = useState([]);

  // Which model fetch is the current one, and which project is open - both
  // read by `modelChanged` on the way back from a request it may have
  // overtaken. See the note there.
  const fetchRef = useRef(0);
  const projectIdRef = useRef(null);
  const fileRef = useRef("model.ldr");

  const project = useMemo(
    () => projects.find((p) => p.id === projectId) || null,
    [projects, projectId]
  );
  projectIdRef.current = projectId;
  const dirty = source !== saved;

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await api.listProjects());
      return true;
    } catch {
      return false;
    }
  }, []);

  // Poll health until the backend answers, then stop. Without this the banner
  // sticks forever if the page was loaded while the backend was restarting.
  useEffect(() => {
    let stop = false;

    const ping = async () => {
      try {
        const h = await api.health();
        if (stop) return true;
        setHealth(h);
        setNotice(null);
        await refreshProjects();
        return true;
      } catch {
        if (!stop) setNotice("Backend is not running on :8000 - retrying…");
        return false;
      }
    };

    ping().then((ok) => {
      if (ok || stop) return;
      const id = setInterval(async () => {
        if (await ping()) clearInterval(id);
      }, 3000);
      return () => clearInterval(id);
    });

    return () => {
      stop = true;
    };
  }, [refreshProjects]);

  /**
   * Source edits live only in the browser until Save is pressed, so anything
   * that replaces what is in the editor has to ask first. Returns false if the
   * user decided to keep editing.
   */
  const confirmDiscard = () =>
    !dirty ||
    window.confirm(
      "You have unsaved changes to the source. Leaving now discards them.\n\n" +
        "Press Cancel to go back and Save first."
    );

  const openProject = async (id, { force = false, view: landing = "model" } = {}) => {
    if (!force && !confirmDiscard()) return;
    setScreen("build");
    setProjectId(id);
    setValidation(null);
    setVersion((v) => v + 1);
    setView(landing);
    setFile("model.ldr");
    fileRef.current = "model.ldr";
    setFiles([]);
    try {
      const text = await api.getModel(id);
      setSource(text);
      setSaved(text);
      api.projectFiles(id).then(({ files: found }) => setFiles(found || []))
        .catch(() => {});
      setValidation(await api.validate(id));
    } catch {
      /* a brand-new project may have nothing to validate yet */
    }
  };

  const newProject = async () => {
    if (!confirmDiscard()) return;
    try {
      const p = await api.createProject("Untitled");
      await refreshProjects();
      // Into the trace, because that is where the composer is and a blank
      // project has nothing to look at yet - an empty baseplate and no way to
      // ask for anything would be the wrong place to land.
      openProject(p.id, { force: true, view: "trace" });
    } catch (e) {
      setNotice(`Could not create the project: ${e.message}`);
    }
  };

  /**
   * The model on disk changed, so bring what is on screen up to date.
   *
   * Ticketed, because subconstructions are built in parallel: three builders
   * writing at once fire this three times, three fetches are in flight
   * together, and they do not come back in the order they were sent. Without a
   * ticket the *slowest* response wins and the editor ends up showing an older
   * model than the one already on disk. The project is checked on the way back
   * too - switching away mid-fetch used to drop one project's model into
   * another's editor.
   */
  const modelChanged = async () => {
    setVersion((v) => v + 1);
    refreshProjects();
    if (!projectId) return;

    const ticket = ++fetchRef.current;
    const forProject = projectId;
    const forFile = fileRef.current;
    try {
      // The components appear one by one while a scene is being built, so the
      // list is re-read every time anything is written, not just on open.
      api
        .projectFiles(forProject)
        .then(({ files: found }) => {
          if (forProject === projectIdRef.current) setFiles(found || []);
        })
        .catch(() => {});

      const text = await api.getFile(forProject, forFile);
      if (ticket !== fetchRef.current || forProject !== projectIdRef.current) return;
      if (forFile !== fileRef.current) return; // they switched files mid-fetch
      setSource(text);
      setSaved(text);
    } catch {
      /* the file will be picked up by the next event, or on reopen */
    }
  };

  /** Show a different file of this project - the scene, or one component. */
  const openFile = async (name) => {
    if (name === file) return;
    if (!confirmDiscard()) return;
    setFile(name);
    fileRef.current = name;
    setVersion((v) => v + 1);
    try {
      const text = await api.getFile(projectId, name);
      if (name !== fileRef.current) return;
      setSource(text);
      setSaved(text);
    } catch (e) {
      setNotice(`Could not open ${name}: ${e.message}`);
    }
  };

  /** The only thing that writes the editor's text to disk. */
  const saveSource = async () => {
    if (file !== "model.ldr") {
      // Components are the agent's working files; the scene is what the
      // project is. Editing one by hand and saving it over the assembled
      // model would be the wrong file written.
      setNotice(
        `${file} is a component the agent built. Switch to model.ldr to save edits.`
      );
      return;
    }
    try {
      await api.putModel(projectId, source);
      setSaved(source);
      setVersion((v) => v + 1);
      setValidation(await api.validate(projectId));
      refreshProjects();
    } catch (e) {
      setNotice(`Could not save: ${e.message}`);
    }
  };

  const remove = async (id, e) => {
    e.stopPropagation();
    if (id === projectId && !confirmDiscard()) return;
    await api.deleteProject(id);
    if (id === projectId) {
      setProjectId(null);
      setSource("");
      setSaved("");
      setValidation(null);
      setView("model");
    }
    refreshProjects();
  };

  const download = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportModel = () =>
    download(new Blob([source], { type: "text/plain" }),
             `${project?.name || "model"}.ldr`);

  /**
   * The booklet is rendered on the backend, a page per build step, and takes a
   * few seconds - hence the flag, which turns the button into its own progress
   * report rather than leaving the click looking ignored.
   */
  const buildInstructions = async () => {
    if (!projectId || building) return;
    setBuilding(true);
    try {
      const { blob, filename } = await api.instructions(projectId);
      download(blob, filename || `${project?.name || "model"} instructions.pdf`);
    } catch (e) {
      setNotice(`Could not build the instructions: ${e.message}`);
    } finally {
      setBuilding(false);
    }
  };

  /**
   * Keep this model in the gallery.
   *
   * Used to be the agent's to call, behind a guess at whether the user's
   * wording counted as asking for it. The shelf is theirs, so the decision is
   * a button; the only thing checked is that the model sits on the grid,
   * because a build nobody can build is not worth keeping.
   */
  const saveToGallery = async () => {
    if (!projectId || saving) return;
    setSaving(true);
    try {
      const { saved } = await api.saveToGallery(projectId, {
        name: project?.name || "Untitled",
      });
      setNotice(`Saved “${saved.name}” to the gallery · ${saved.total_pieces} pieces.`);
    } catch (e) {
      setNotice(`Not saved - ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const onImported = async (created) => {
    setImporting(false);
    await refreshProjects();
    if (created) openProject(created.id, { force: true });
  };

  /** A creation opens as its own project, so editing never touches the library. */
  const openCreation = async (created) => {
    await refreshProjects();
    await openProject(created.id, { force: true }); // lands in Model view
    setNotice(`Opened a copy of “${created.name}” as a project.`);
  };

  /** A project started from an official set - a copy, on the workbench. */
  const openFromSet = async (projectId) => {
    setScreen("build");
    await refreshProjects();
    await openProject(projectId, { force: true });
    setNotice("Opened a copy of that set as a project. The set itself is untouched.");
  };

  const patchProject = async (id, patch) => {
    try {
      await api.updateProject(id, patch);
      await refreshProjects();
    } catch (e) {
      setNotice(`Could not update the project: ${e.message}`);
    }
  };

  // Stable, because the dialog re-reads the settings whenever `onClose`
  // changes identity - an inline arrow would reset it on every render of App.
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  /**
   * Which piece the caret is on, held only while it is a different one.
   *
   * The editor reports on every caret move, which is every keystroke. Keeping
   * the object it hands over would change identity each time and the viewer
   * would tear its highlight down and build it again - twenty times a second
   * while anybody is typing. The key says whether it is the same piece.
   */
  const onCursor = useCallback((ref) => {
    setCursorPart((prev) => (prev?.key === (ref?.key ?? null) ? prev : ref || null));
  }, []);

  /** Everything the rail was showing has just gone; so has whatever was open. */
  const projectsErased = useCallback(() => {
    setProjectId(null);
    setSource("");
    setSaved("");
    setValidation(null);
    setView("model");
    refreshProjects();
  }, [refreshProjects]);

  /** One button per shelf, each of them also the way back off it. */
  const toggleScreen = (name) => {
    if (screen === name) setScreen("build");
    else if (confirmDiscard()) setScreen(name);
  };

  return (
    <div className="app">
      <TopBar
        project={project}
        modified={project?.modified}
        view={view}
        onView={(next) => {
          // leaving the editor throws the text away, same as any other exit
          if (next === view || next === "source" || confirmDiscard()) setView(next);
        }}
        onNew={newProject}
        onUpload={() => setImporting(true)}
        onExport={exportModel}
        onInstructions={buildInstructions}
        building={building}
        onSaveToGallery={saveToGallery}
        saving={saving}
        onSave={saveSource}
        onGallery={() => toggleScreen("gallery")}
        onParts={() => toggleScreen("parts")}
        onSets={() => toggleScreen("sets")}
        onSettings={() => setSettingsOpen(true)}
        onRename={(id, name) => patchProject(id, { name })}
        screen={screen}
        dirty={dirty}
        lines={source.split("\n").length}
        health={health}
      />

      {notice && (
        <div className="notice">
          {notice}
          <button className="btn btn--icon notice-close" onClick={() => setNotice(null)}>
            ×
          </button>
        </div>
      )}

      {screen === "gallery" ? (
        <Gallery onOpen={openCreation} />
      ) : screen === "parts" ? (
        <PartsGallery />
      ) : screen === "sets" ? (
        <SetsGallery onOpenProject={openFromSet} />
      ) : (
      <div className="body">
        <ProjectRail
          projects={projects}
          projectId={projectId}
          validation={projectId ? validation : null}
          onOpen={openProject}
          onDelete={remove}
          onPatch={patchProject}
          onCheckHover={setCheck}
        />

        <main className={`stage stage--${view}`}>
          {projectId ? (
            <ErrorBoundary>
              {view === "source" && (
                <SourceEditor
                  name={project?.name || "model"}
                  value={source}
                  onChange={setSource}
                  onSave={saveSource}
                  dirty={dirty}
                  validation={validation}
                  files={files}
                  file={file}
                  onFile={openFile}
                  onCursor={onCursor}
                />
              )}
              {/* The trace takes the whole workbench: it is a graph the width
                  of the run, and a preview beside it would only shrink it. */}
              {view === "trace" ? (
                <TraceView projectId={projectId} runId={runId} />
              ) : (
                <Viewer3D
                  projectId={projectId}
                  version={version}
                  file={file}
                  busy={running}
                  built={!!validation?.passed}
                  // Only in the source view: the piece the caret is sitting on,
                  // so the line you are reading and the brick it places are
                  // the same object rather than two lists to match up by hand.
                  highlight={view === "source" ? cursorPart : null}
                  // And in the model view: the parts behind whichever build
                  // check the mouse is resting on in the rail. Only there -
                  // the two light up bricks by swapping the same materials,
                  // so letting both run over the source view would have each
                  // putting back what the other took.
                  check={view === "model" ? check : null}
                  validation={validation}
                >
                  {view === "source" && <PartsUsed source={source} />}
                </Viewer3D>
              )}

              {/* The one box the agent is spoken to through. It floats over
                  the model and over the trace - the two views you would
                  actually ask for a change from. Not over the source: that is
                  a text editor, and a box over it is in the way of the typing.
                  Mounted either way, so a build started here keeps being
                  watched while the source is being read. */}
              <Composer
                projectId={projectId}
                visible={view !== "source"}
                onModelChanged={modelChanged}
                onValidation={setValidation}
                onActivity={setRunning}
                onRun={setRunId}
              />
            </ErrorBoundary>
          ) : (
            <EmptyStage onNew={newProject} onUpload={() => setImporting(true)} />
          )}
        </main>
      </div>
      )}

      <ImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        onImported={onImported}
      />

      <SettingsDialog
        open={settingsOpen}
        onClose={closeSettings}
        projectCount={projects.length}
        onProjectsErased={projectsErased}
      />
    </div>
  );
}

