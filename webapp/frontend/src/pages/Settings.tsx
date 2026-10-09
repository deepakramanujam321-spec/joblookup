import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useIntegrationMutations, useIntegrations, useKnowledge } from "../api/hooks";
import type { IntegrationService } from "../api/types";
import { Badge, EmptyState, ErrorState, Skeleton, Time, errorMessage, useConfirm, useToast } from "../components/ui";

declare global {
  interface Window { gapi?: { load: (name: string, cb: () => void) => void }; google?: { picker: any } } // eslint-disable-line @typescript-eslint/no-explicit-any
}

export function SettingsPage() {
  const integrations = useIntegrations();
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  useEffect(() => {
    const connected = params.get("connected");
    const failed = params.get("integration_error");
    if (connected) toast(`Google ${connected === "gmail" ? "Gmail" : "Drive"} connected`);
    if (failed) toast(`Connection failed: ${failed}`, "error");
    if (connected || failed) setParams({}, { replace: true });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-head"><div><h1>Settings & integrations</h1><p>Optional connections. JobLookup works without them.</p></div></div>
      {integrations.isLoading && <Skeleton height={200} />}
      {integrations.error && <ErrorState error={integrations.error} onRetry={integrations.refetch} />}
      {integrations.data && !integrations.data.configured && (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          <div>Google integration isn't configured on the server yet. Create an OAuth client in Google Cloud, set <code>GOOGLE_CLIENT_ID</code>, <code>GOOGLE_CLIENT_SECRET</code> and <code>TOKEN_ENCRYPTION_KEY</code> on the server, and add this redirect URI:<br /><code>{integrations.data.redirect_uri}</code></div>
        </div>
      )}
      {integrations.data && (
        <div className="two-col">
          <div className="stack">
            <ServiceCard service="gmail" name="Gmail" state={integrations.data.services.gmail} configured={integrations.data.configured}
              description="Save application emails as Gmail drafts so you can review and send them yourself. JobLookup can create and update drafts only. It can't read your inbox and it never sends anything." />
            <ServiceCard service="drive" name="Google Drive" state={integrations.data.services.drive} configured={integrations.data.configured}
              description="Import resumes and career documents you pick. JobLookup can only see files you select in Google's picker: no folder crawling, and nothing in Drive is modified." />
          </div>
          <DrivePanel connected={integrations.data.services.drive?.status === "connected"} pickerReady={integrations.data.picker_ready} />
        </div>
      )}
    </>
  );
}

function ServiceCard({ service, name, state, configured, description }: { service: "gmail" | "drive"; name: string; state: IntegrationService | null; configured: boolean; description: string }) {
  const m = useIntegrationMutations();
  const confirm = useConfirm();
  const toast = useToast();
  const connected = state?.status === "connected";
  return (
    <section className="card card-pad stack tight" aria-label={name}>
      <div className="row between">
        <h2>{name}</h2>
        {state ? <Badge tone={connected ? "good" : "bad"}>{connected ? "Connected" : state.status === "revoked" ? "Needs reconnecting" : "Error"}</Badge> : <Badge>Not connected</Badge>}
      </div>
      <p className="small muted">{description}</p>
      {state?.account_email && <p className="small">Account: <strong>{state.account_email}</strong> · connected <Time iso={state.connected_at} /></p>}
      {state?.last_error && <div className="notice bad small">{state.last_error}</div>}
      <div className="row">
        {!connected && <button className="btn primary" disabled={!configured || m.connect.isPending} onClick={() => m.connect.mutate(service, { onError: (e) => toast(errorMessage(e), "error") })}>{state ? "Reconnect" : "Connect"}</button>}
        {state && (
          <button className="btn danger" onClick={async () => {
            const deleteImported = service === "drive" && (await confirm({ title: "Also delete imported documents?", body: "Remove JobLookup's copies of documents imported from Drive? (Files in Drive are never touched.)", confirmLabel: "Delete copies too" }));
            if (await confirm({ title: `Disconnect ${name}?`, body: "Stored credentials are deleted from JobLookup.", confirmLabel: "Disconnect", danger: true })) {
              m.disconnect.mutate({ service, deleteImported }, { onSuccess: () => toast(`${name} disconnected`), onError: (e) => toast(errorMessage(e), "error") });
            }
          }}>Disconnect</button>
        )}
      </div>
    </section>
  );
}

function loadPicker(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.google?.picker) return resolve();
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.onload = () => window.gapi!.load("picker", () => resolve());
    script.onerror = () => reject(new Error("Couldn't load Google Picker"));
    document.body.appendChild(script);
  });
}

function DrivePanel({ connected, pickerReady }: { connected: boolean; pickerReady: boolean }) {
  const docs = useKnowledge();
  const m = useIntegrationMutations();
  const toast = useToast();
  const [kind, setKind] = useState<"resume" | "knowledge">("knowledge");
  const pick = async () => {
    try {
      const cfg = await api.get<{ api_key: string; app_id: string; access_token: string }>("/api/v2/integrations/drive/picker");
      await loadPicker();
      const g = window.google!.picker;
      const view = new g.DocsView(g.ViewId.DOCS).setMimeTypes("application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.google-apps.document,text/plain");
      new g.PickerBuilder().addView(view).enableFeature(g.Feature.MULTISELECT_ENABLED).setOAuthToken(cfg.access_token)
        .setDeveloperKey(cfg.api_key).setAppId(cfg.app_id)
        .setCallback((data: { action: string; docs?: { id: string }[] }) => {
          if (data.action !== g.Action.PICKED || !data.docs?.length) return;
          m.importDrive.mutate({ file_ids: data.docs.map((d) => d.id).slice(0, 10), kind }, {
            onSuccess: (r) => {
              const failed = r.results.filter((x) => !x.ok);
              toast(failed.length ? `Imported ${r.results.length - failed.length}; failed: ${failed.map((f) => f.error).join("; ")}` : `Imported ${r.results.length} file(s)`, failed.length ? "error" : "info");
            },
            onError: (e) => toast(errorMessage(e), "error"),
          });
        }).build().setVisible(true);
    } catch (e) {
      toast(errorMessage(e), "error");
    }
  };
  return (
    <section className="card card-pad stack" aria-label="Imported documents">
      <h2>Documents from Drive</h2>
      {!connected ? <p className="small muted">Connect Google Drive to import documents.</p> : !pickerReady ? (
        <div className="notice warn small">The Drive picker also needs <code>GOOGLE_API_KEY</code> and <code>GOOGLE_APP_ID</code> on the server.</div>
      ) : (
        <div className="row wrap">
          <select className="select" style={{ width: "auto" }} aria-label="Import as" value={kind} onChange={(e) => setKind(e.target.value as "resume" | "knowledge")}>
            <option value="knowledge">Import as career document</option><option value="resume">Import as resume</option>
          </select>
          <button className="btn primary" disabled={m.importDrive.isPending} onClick={pick}>{m.importDrive.isPending ? "Importing…" : "Choose files…"}</button>
        </div>
      )}
      <p className="hint">Career documents (project write-ups, performance reviews, certificates) give drafting more real evidence to draw on. Imported resumes appear on your Profile page.</p>
      {docs.data?.items.length === 0 && <EmptyState title="No documents imported" />}
      {docs.data?.items.map((d) => (
        <div key={d.id} className="row between">
          <div className="grow"><strong className="ellipsis">{d.name}</strong>
            <div className="small muted">{d.status === "done" ? `${d.chars ?? 0} characters` : `Failed: ${d.error}`} · imported <Time iso={d.imported_at} /></div></div>
          {connected && <button className="btn sm" onClick={() => m.refreshDoc.mutate(d.id, { onSuccess: (r) => toast(r.changed ? "Updated from Drive" : "No changes in Drive"), onError: (e) => toast(errorMessage(e), "error") })}>Refresh</button>}
          <button className="btn ghost sm danger" onClick={() => m.deleteDoc.mutate(d.id)}>Remove</button>
        </div>
      ))}
    </section>
  );
}
