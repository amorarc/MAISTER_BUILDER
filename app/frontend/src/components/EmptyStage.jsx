import BrickLogo from "./BrickLogo";

/** Nothing open yet: a bobbing brick on a bare baseplate. */
export default function EmptyStage({ onNew, onUpload }) {
  return (
    <div className="empty-stage">
      <div className="empty-inner">
        <BrickLogo hero />
        <h2 className="empty-title">Nothing on the baseplate</h2>
        <p className="empty-text">
          Start a blank project and describe what you want, or drop in an <code>.ldr</code>{" "}
          file you already have.
        </p>
        <div className="empty-actions">
          <button className="btn btn--primary btn--big" onClick={onNew}>
            New project
          </button>
          <button className="btn btn--light btn--big" onClick={onUpload}>
            Upload .ldr
          </button>
        </div>
      </div>
    </div>
  );
}
