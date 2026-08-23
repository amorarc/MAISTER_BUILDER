// Override with VITE_API to point the UI at a backend on another port.
export const API = import.meta.env.VITE_API || "http://localhost:8000";

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => fetch(`${API}/api/health`).then(json),

  listProjects: () => fetch(`${API}/api/projects`).then(json),

  createProject: (name) =>
    fetch(`${API}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then(json),

  uploadProject: (file) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${API}/api/projects/upload`, { method: "POST", body: form }).then(json);
  },

  // rename, or recolour the brick beside it - pass colour: "" to go back to the
  // colour derived from the project id
  updateProject: (id, patch) =>
    fetch(`${API}/api/projects/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(json),

  resetChat: (id) =>
    fetch(`${API}/api/projects/${id}/chat/reset`, { method: "POST" }).then(json),

  // Reference images: a picture of what the model should look like. They
  // belong to the project, not to the message that carried them, so they are
  // still in force on later turns.
  listReferences: (id) => fetch(`${API}/api/projects/${id}/references`).then(json),

  addReference: (id, file) => {
    const form = new FormData();
    // A clipboard paste arrives as a Blob with no name; multipart needs one.
    form.append("file", file, file.name || "pasted.png");
    return fetch(`${API}/api/projects/${id}/references`, {
      method: "POST",
      body: form,
    }).then(json);
  },

  deleteReference: (id, imageId) =>
    fetch(`${API}/api/projects/${id}/references/${imageId}`, {
      method: "DELETE",
    }).then(json),

  referenceUrl: (id, imageId) => `${API}/api/projects/${id}/references/${imageId}`,

  deleteProject: (id) =>
    fetch(`${API}/api/projects/${id}`, { method: "DELETE" }).then(json),

  // every project at once - there is no undo, so only Settings offers it
  deleteAllProjects: () =>
    fetch(`${API}/api/projects`, { method: "DELETE" }).then(json),

  // which model the agent runs on, and which provider serves it
  settings: () => fetch(`${API}/api/settings`).then(json),

  saveSettings: (patch) =>
    fetch(`${API}/api/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(json),

  /** Every project as one zip. Returns the blob and the name to save it under. */
  archive: async () => {
    const res = await fetch(`${API}/api/projects/archive.zip`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* keep statusText */
      }
      throw new Error(detail);
    }
    const disposition = res.headers.get("content-disposition") || "";
    const filename = /filename="([^"]+)"/i.exec(disposition)?.[1];
    return { blob: await res.blob(), filename };
  },

  // A scene is built as several files - model.ldr plus one per
  // subconstruction under parts/ - and during a build the components are where
  // all the work is. The Source view lists them with this.
  projectFiles: (id) => fetch(`${API}/api/projects/${id}/files`).then(json),

  getFile: (id, name) =>
    fetch(`${API}/api/projects/${id}/file?name=${encodeURIComponent(name)}`).then(
      (r) => {
        if (!r.ok) throw new Error("could not load that file");
        return r.text();
      }
    ),

  fileUrl: (id, name, v) =>
    `${API}/api/projects/${id}/file?name=${encodeURIComponent(name)}&v=${v}`,

  getModel: (id) =>
    fetch(`${API}/api/projects/${id}/model`).then((r) => {
      if (!r.ok) throw new Error("could not load model");
      return r.text();
    }),

  putModel: (id, content) =>
    fetch(`${API}/api/projects/${id}/model`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).then(json),

  /** Put this project's model in the gallery. Refused unless it validates. */
  saveToGallery: (id, patch = {}) =>
    fetch(`${API}/api/projects/${id}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(json),

  // the agent's own library - models it built and chose to keep
  listCreations: () => fetch(`${API}/api/creations`).then(json),

  openCreation: (creationId) =>
    fetch(`${API}/api/creations/${creationId}/open`, { method: "POST" }).then(json),

  creationModelUrl: (creationId) => `${API}/api/creations/${creationId}/model.ldr`,

  deleteCreation: (creationId) =>
    fetch(`${API}/api/creations/${creationId}`, { method: "DELETE" }).then(json),

  // the parts bin - the catalogue the agent builds out of, as opposed to the
  // gallery of things it has built
  browseParts: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== "") query.set(k, v);
    });
    return fetch(`${API}/api/parts?${query}`).then(json);
  },

  partCategories: () => fetch(`${API}/api/parts/categories`).then(json),

  // the ways a part can join to another part - stud and tube, clip and bar,
  // Technic pin, and the rest
  partConnections: () => fetch(`${API}/api/parts/connections`).then(json),

  partDetails: (partId) => fetch(`${API}/api/parts/${partId}`).then(json),

  /** One part on its own, as a model LDrawLoader can draw. */
  partModelUrl: (partId, colour = 4) =>
    `${API}/api/parts/${partId}/model.ldr?colour=${colour}`,

  // the shelf of official sets - 1,800 real models, which is what the builder
  // learns from and what a person can start a project out of
  browseSets: (params = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== "") query.set(k, v);
    });
    return fetch(`${API}/api/sets?${query}`).then(json);
  },

  setThemes: () => fetch(`${API}/api/sets/themes`).then(json),

  setDetails: (number) =>
    fetch(`${API}/api/sets/${encodeURIComponent(number)}`).then(json),

  /** A set's LDraw source, for the thumbnail renderer and the viewer. */
  setModelUrl: (number) =>
    `${API}/api/sets/${encodeURIComponent(number)}/model.ldr`,

  /** Start a new project with this set as its starting point. */
  projectFromSet: (number, name) =>
    fetch(`${API}/api/sets/${encodeURIComponent(number)}/open`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name || null }),
    }).then(json),

  validate: (id) =>
    fetch(`${API}/api/projects/${id}/validate`, { method: "POST" }).then(json),

  // maxSteps 0 = no limit; a run ends on finish, give-up or stop
  chat: (id, message, maxSteps = 0) =>
    fetch(`${API}/api/projects/${id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, max_steps: maxSteps }),
    }).then(json),

  // `since` is the event index already seen, so a poll only returns what is new
  run: (runId, since = 0) => fetch(`${API}/api/runs/${runId}?since=${since}`).then(json),

  // The run still in flight for a project, so a page that has forgotten one -
  // after a reload, a project switch, or in a second tab - can pick it back up.
  activeRun: (id) => fetch(`${API}/api/projects/${id}/run`).then(json),

  stopRun: (runId) => fetch(`${API}/api/runs/${runId}/stop`, { method: "POST" }).then(json),

  // The conversation, kept on the server so every window reads the same one.
  // Only the server appends to it; `putMessages` exists to carry over a thread
  // left in this browser's storage from before that was true.
  messages: (id) => fetch(`${API}/api/projects/${id}/messages`).then(json),

  putMessages: (id, messages) =>
    fetch(`${API}/api/projects/${id}/messages`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    }).then(json),

  // What the agent actually did, kept after the run that did it. `traceGraph`
  // is readable while a run is still going - it grows as the events land.
  traces: (id) => fetch(`${API}/api/projects/${id}/traces`).then(json),

  traceGraph: (id, runId) =>
    fetch(`${API}/api/projects/${id}/traces/${runId}`).then(json),

  // A picture as it was when the run took it. Copies, kept with the trace -
  // out/renders holds only the newest build of each project.
  traceImageUrl: (id, name) => `${API}/api/projects/${id}/traces/images/${name}`,

  clearTraces: (id) =>
    fetch(`${API}/api/projects/${id}/traces`, { method: "DELETE" }).then(json),

  /** Build the instruction booklet and hand back the PDF itself. */
  instructions: async (id) => {
    const res = await fetch(`${API}/api/projects/${id}/instructions`, {
      method: "POST",
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* keep statusText */
      }
      throw new Error(detail);
    }
    // Starlette sends the RFC 5987 form (filename*=utf-8''…) whenever the name
    // needs escaping, which a project name with spaces in it does.
    const disposition = res.headers.get("content-disposition") || "";
    const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition);
    const plain = /filename="([^"]+)"/i.exec(disposition);
    const filename = encoded
      ? decodeURIComponent(encoded[1])
      : plain?.[1];
    return { blob: await res.blob(), filename };
  },

  // cache-busted so the viewer always refetches after an edit
  modelUrl: (id, v) => `${API}/api/projects/${id}/model.ldr?v=${v}`,
  libraryPath: () => `${API}/ldraw/`,
};
