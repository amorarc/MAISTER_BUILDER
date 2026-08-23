import { Component } from "react";

/**
 * Without this, any render-time throw unmounts the whole tree and the page goes
 * blank with no explanation. Show the error and offer a reload instead.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Maister Builder crashed:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash">
        <h2>A brick came loose</h2>
        <p>Something threw while rendering the viewer.</p>
        <pre>{String(this.state.error?.stack || this.state.error)}</pre>
        <div className="crash-actions">
          <button className="btn btn--primary" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
          <button className="btn btn--quiet" onClick={() => window.location.reload()}>
            Hard reload
          </button>
        </div>
        <p>
          If this appeared after a code change, the tab may be running stale
          modules — reload with <code>Ctrl+Shift+R</code>.
        </p>
      </div>
    );
  }
}
