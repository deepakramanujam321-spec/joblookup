import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode; resetKey?: string }
interface State { error: Error | null }

/** Catches render errors under it so one broken page shows a message instead
 *  of unmounting the whole app (a blank white page). Resets when `resetKey`
 *  changes, e.g. on navigation. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crashed:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="card card-pad stack" role="alert">
        <h2>Something went wrong on this page</h2>
        <p className="small muted">The rest of the app still works. Reloading usually fixes it; if it keeps happening, this message helps to find the cause.</p>
        <pre className="small" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{error.message || String(error)}</pre>
        <div className="row">
          <button className="btn primary" onClick={() => window.location.reload()}>Reload</button>
          <a className="btn" href="/">Back to dashboard</a>
        </div>
      </div>
    );
  }
}
