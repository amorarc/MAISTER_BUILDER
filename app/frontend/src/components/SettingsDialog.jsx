import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { plural } from "../format";

// How long "Erase all projects" stays armed before it forgets it was asked.
// Long enough to mean it, short enough that a stray second click a minute later
// does not wipe the workbench.
const ARM_MS = 6000;

/** The provider label, since the routing policies are not providers at all. */
const PROVIDER_LABELS = {
  "": "Router's pick",
  cheapest: "Cheapest",
  fastest: "Fastest",
};

function label(provider) {
  return PROVIDER_LABELS[provider] ?? provider;
}

/**
 * Two models and their providers, plus the two things that can be done to
 * every project at once.
 *
 * The models are separate on purpose. The builder writes LDraw and is
 * text-only; the vision model looks at the renders of what it wrote and says
 * whether it resembles what was asked for. One id could never do both jobs.
 *
 * All of it lives on the backend - it is the backend that runs the agent, so a
 * choice kept only in this tab would be a preference the thing it configures
 * never sees.
 */
export default function SettingsDialog({ open, onClose, projectCount, onProjectsErased }) {
  const [data, setData] = useState(null); // what the backend offers
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  // The second model: the one that looks at the renders. It has to be
  // multimodal, and it is a separate choice from the one that builds.
  const [visionModel, setVisionModel] = useState("");
  const [visionProvider, setVisionProvider] = useState("");
  // Whether the agent may lift assemblies straight out of released sets.
  // Turning it off is how you find out what the agent designs rather than what
  // the reference corpus already contains.
  const [copyFromSet, setCopyFromSet] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [zipping, setZipping] = useState(false);
  const [erasing, setErasing] = useState(false);
  const [armed, setArmed] = useState(false); // erase, asked once
  const [done, setDone] = useState(null); // a one-line receipt for the last action
  const cardRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let stale = false;

    setError(null);
    setDone(null);
    setArmed(false);
    api
      .settings()
      .then((s) => {
        if (stale) return;
        setData(s);
        setModel(s.model);
        setProvider(s.provider);
        setVisionModel(s.vision_model ?? "");
        setVisionProvider(s.vision_provider ?? "");
        setCopyFromSet(s.copy_from_set !== false);
      })
      .catch((e) => !stale && setError(e.message));

    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    cardRef.current?.focus();
    return () => {
      stale = true;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  // The armed state disarms itself, so the dialog is never left holding a
  // primed destructive button while the user is doing something else.
  useEffect(() => {
    if (!armed) return;
    const id = setTimeout(() => setArmed(false), ARM_MS);
    return () => clearTimeout(id);
  }, [armed]);

  if (!open) return null;

  const busy = saving || zipping || erasing;
  const trimmed = model.trim();
  const visionTrimmed = visionModel.trim();
  const join = (id, p) => (id && p ? `${id}:${p}` : id);
  const effective = join(trimmed, provider);
  const visionEffective = join(visionTrimmed, visionProvider);
  const changed =
    !!data &&
    (trimmed !== data.model ||
      provider !== data.provider ||
      visionTrimmed !== data.vision_model ||
      visionProvider !== data.vision_provider ||
      copyFromSet !== (data.copy_from_set !== false));

  const save = async () => {
    if (!trimmed || !visionTrimmed || busy) return;
    setSaving(true);
    setError(null);
    try {
      const next = await api.saveSettings({
        model: trimmed,
        provider,
        vision_model: visionTrimmed,
        vision_provider: visionProvider,
        copy_from_set: copyFromSet,
      });
      setData(next);
      onClose();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const downloadAll = async () => {
    if (busy) return;
    setZipping(true);
    setError(null);
    try {
      const { blob, filename } = await api.archive();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || "maister-projects.zip";
      a.click();
      URL.revokeObjectURL(url);
      setDone(`Downloaded ${plural(projectCount, "project")}.`);
    } catch (e) {
      setError(e.message);
    } finally {
      setZipping(false);
    }
  };

  const eraseAll = async () => {
    if (busy) return;
    if (!armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    setErasing(true);
    setError(null);
    try {
      const { deleted } = await api.deleteAllProjects();
      setDone(`Erased ${plural(deleted, "project")}.`);
      onProjectsErased?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setErasing(false);
    }
  };

  // a provider set by hand, or one the backend has since stopped listing, is
  // still the one in force - show it rather than quietly dropping the selection
  const providersFor = (current) => {
    if (!data) return [];
    const list = ["", ...data.policies, ...data.providers];
    if (current && !list.includes(current)) list.push(current);
    return list;
  };
  const providers = providersFor(provider);
  const visionProviders = providersFor(visionProvider);

  return (
    <div
      className="modal"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        tabIndex={-1}
        ref={cardRef}
      >
        <div className="modal-head">
          <div className="modal-mark modal-mark--yellow" />
          <div style={{ flex: 1 }}>
            <h2 className="modal-title">Settings</h2>
            <p className="modal-sub">
              Two models - one builds, one looks at what was built - and what
              the builder is allowed to borrow. Saved on the backend, so they
              survive a restart.
            </p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close" disabled={busy}>
            ✕
          </button>
        </div>

        {!data && !error && <div className="set-loading">Reading the settings…</div>}

        {data && (
          <>
            <section className="set-field">
              <label className="set-label" htmlFor="set-model">
                BUILDER MODEL
              </label>
              <input
                id="set-model"
                className="set-input"
                value={model}
                spellCheck={false}
                autoComplete="off"
                placeholder={data.default_model}
                onChange={(e) => setModel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                }}
              />
              <div className="set-hint">
                Any model id the HuggingFace router can reach. A few that work:
              </div>
              <div className="set-picks">
                {data.suggested_models.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className={`pick pick--wide ${id === trimmed ? "is-on" : ""}`}
                    onClick={() => setModel(id)}
                  >
                    {id.split("/").pop()}
                  </button>
                ))}
              </div>
            </section>

            <section className="set-field">
              <span className="set-label">PROVIDER</span>
              <div className="set-picks">
                {providers.map((p) => (
                  <button
                    key={p || "auto"}
                    type="button"
                    className={`pick ${p === provider ? "is-on" : ""} ${
                      p === "" ? "pick--auto" : ""
                    }`}
                    onClick={() => setProvider(p)}
                  >
                    {label(p)}
                  </button>
                ))}
              </div>
              <div className="set-hint">
                <b>Cheapest</b> and <b>Fastest</b> let the router choose on price
                or latency. Naming one pins every call to it.
              </div>
            </section>

            <div className="set-effective">
              <span className="eyebrow">SENDING AS</span>
              <code>{effective || "-"}</code>
            </div>

            <section className="set-field">
              <label className="set-label" htmlFor="set-vision-model">
                VISION MODEL
              </label>
              <input
                id="set-vision-model"
                className="set-input"
                value={visionModel}
                spellCheck={false}
                autoComplete="off"
                placeholder={data.default_vision_model}
                onChange={(e) => setVisionModel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                }}
              />
              <div className="set-hint">
                The model that <b>looks</b> at the renders and reports whether
                the build resembles what was asked for. It must be multimodal -
                a text-only id here means the agent builds blind.
              </div>
              <div className="set-picks">
                {(data.suggested_vision_models ?? []).map((id) => (
                  <button
                    key={id}
                    type="button"
                    className={`pick pick--wide ${id === visionTrimmed ? "is-on" : ""}`}
                    onClick={() => setVisionModel(id)}
                  >
                    {id.split("/").pop()}
                  </button>
                ))}
              </div>
            </section>

            <section className="set-field">
              <span className="set-label">VISION PROVIDER</span>
              <div className="set-picks">
                {visionProviders.map((p) => (
                  <button
                    key={p || "auto"}
                    type="button"
                    className={`pick ${p === visionProvider ? "is-on" : ""} ${
                      p === "" ? "pick--auto" : ""
                    }`}
                    onClick={() => setVisionProvider(p)}
                  >
                    {label(p)}
                  </button>
                ))}
              </div>
            </section>

            <div className="set-effective">
              <span className="eyebrow">LOOKING WITH</span>
              <code>{visionEffective || "-"}</code>
            </div>

            {data.renderer_available === false && (
              <div className="set-hint">
                LeoCAD is not installed, so nothing can be rendered and this
                model is never called. See <code>simulator/README.md</code>.
              </div>
            )}

            <section className="set-field">
              <span className="set-label">COPYING FROM REAL SETS</span>
              <div className="set-picks">
                <button
                  type="button"
                  className={`pick ${copyFromSet ? "is-on" : ""}`}
                  onClick={() => setCopyFromSet(true)}
                >
                  Allowed
                </button>
                <button
                  type="button"
                  className={`pick ${!copyFromSet ? "is-on" : ""}`}
                  onClick={() => setCopyFromSet(false)}
                >
                  Off
                </button>
              </div>
              <div className="set-hint">
                {copyFromSet ? (
                  <>
                    The agent can lift a whole assembly out of a released set -
                    a wing, a wheel arch, a torso - and build on top of it.
                    Better models, faster; less of the model is its own.
                  </>
                ) : (
                  <>
                    <b>The agent designs every part placement itself.</b> It can
                    still read the 1,800 reference sets for technique, but it
                    cannot graft from them. Expect simpler models - this is the
                    setting that shows you what it actually invents.
                  </>
                )}
              </div>
            </section>

            <section className="set-field set-field--danger">
              <span className="set-label">
                ALL PROJECTS
                <span className="set-count">{plural(projectCount, "project")}</span>
              </span>
              <div className="set-actions">
                <button
                  className="btn btn--quiet"
                  onClick={downloadAll}
                  disabled={busy || projectCount === 0}
                >
                  {zipping ? "Zipping…" : "Download all as .zip"}
                </button>
                <button
                  className={`btn ${armed ? "btn--danger" : "btn--quiet"}`}
                  onClick={eraseAll}
                  disabled={busy || projectCount === 0}
                >
                  {erasing
                    ? "Erasing…"
                    : armed
                      ? `Erase ${plural(projectCount, "project")} - sure?`
                      : "Erase all projects"}
                </button>
              </div>
              <div className="set-hint">
                {armed
                  ? "Click again to erase. Nothing here comes back - take the zip first."
                  : "The zip holds one .ldr per project. Erasing cannot be undone."}
              </div>
            </section>
          </>
        )}

        {error && <div className="set-error">{error}</div>}
        {done && !error && <div className="set-done">{done}</div>}

        <div className="modal-foot">
          <p>The agent picks the change up on its next message.</p>
          <button className="btn btn--quiet" onClick={onClose} disabled={busy}>
            {changed ? "Cancel" : "Close"}
          </button>
          <button
            className="btn btn--primary btn--big"
            onClick={save}
            disabled={busy || !trimmed || !visionTrimmed || !changed}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
