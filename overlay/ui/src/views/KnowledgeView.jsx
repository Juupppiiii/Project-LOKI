import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api.js";
import {
  Callout,
  EmptyState,
  Message,
  Panel,
  RiskBadge,
} from "../components/primitives.jsx";
import { shortTime } from "../vocabulary.js";
import CredentialsPanel from "./CredentialsPanel.jsx";

/* Knowledge: the library of handbooks, and the access the agent may use.
 *
 * Left  - upload, then every file that has been uploaded.
 * Right - the selected handbook (its text and the procedures it produced), or
 *         the access credentials centre.
 */

export default function KnowledgeView({ onRefresh, busy }) {
  const [mode, setMode] = useState("handbooks"); // handbooks | access

  const [formats, setFormats] = useState(null);
  const [sources, setSources] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [content, setContent] = useState(null);
  const [detail, setDetail] = useState({});

  const [file, setFile] = useState(null);
  const [uploadMsg, setUploadMsg] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  const [actionMsg, setActionMsg] = useState(null);

  const load = useCallback(async () => {
    const s = await api.sources();
    setSources(s.sources);
    return s.sources;
  }, []);

  useEffect(() => {
    api.formats().then(setFormats).catch(() => setFormats(null));
    load().catch(() => {});
  }, [load]);

  // Load the selected file's text and the review detail for its procedures.
  useEffect(() => {
    if (!selectedId) {
      setContent(null);
      setDetail({});
      return;
    }
    let alive = true;

    api
      .sourceContent(selectedId)
      .then((c) => alive && setContent(c))
      .catch(() => alive && setContent(null));

    const src = sources.find((s) => s.source_id === selectedId);
    const pending = (src?.procedures || []).filter((p) => p.status === "draft");
    Promise.all(pending.map((p) => api.draft(p.draft_id).catch(() => null))).then(
      (rows) => {
        if (!alive) return;
        const map = {};
        for (const r of rows) if (r) map[r.draft_id] = r;
        setDetail(map);
      }
    );

    return () => {
      alive = false;
    };
  }, [selectedId, sources]);

  async function doUpload() {
    if (!file) {
      setUploadMsg({ kind: "error", text: "Choose a file first." });
      return;
    }
    setUploading(true);
    setUploadMsg({ kind: "", text: "Reading the file…" });
    try {
      // Everything uploaded here is a handbook. Uploads become drafts; nothing
      // changes until a person publishes.
      const res = await api.upload(file, "handbook");
      setUploadMsg({
        kind: "ok",
        text: `${res.drafts.length} procedure(s) found. Nothing is active until you publish.`,
      });
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      const fresh = await load();
      setSelectedId(res.source.source_id);
      if (!fresh.some((s) => s.source_id === res.source.source_id)) await load();
      await onRefresh();
    } catch (err) {
      setUploadMsg({ kind: "error", text: err.message });
    } finally {
      setUploading(false);
    }
  }

  async function act(fn, okText) {
    setActionMsg({ kind: "", text: "Working…" });
    try {
      const r = await fn();
      setActionMsg({
        kind: "ok",
        text: r?.procedures_loaded
          ? `${okText} ${r.procedures_loaded} procedures now active.`
          : okText,
      });
      await load();
      await onRefresh();
    } catch (err) {
      setActionMsg({ kind: "error", text: err.message });
    }
  }

  const selected = sources.find((s) => s.source_id === selectedId) || null;
  const accept = formats
    ? formats.handbook.map((f) => `.${f}`).join(",")
    : undefined;

  return (
    <div className="layout layout-wide">
      {/* ------------------- left: upload + library ------------------- */}
      <Panel title="Handbooks" scroll={false}>
        <div style={{ padding: "var(--sp-5)", borderBottom: "1px solid var(--line)" }}>
          <input
            id="upload-file"
            ref={fileRef}
            type="file"
            accept={accept}
            style={{ display: "none" }}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <label htmlFor="upload-file" className="upload-drop">
            {file ? (
              <>
                <strong>{file.name}</strong>
                <div className="hint" style={{ marginTop: ".2rem" }}>
                  {(file.size / 1024).toFixed(1)} KB · click to choose another
                </div>
              </>
            ) : (
              <>
                <strong>Choose a file to upload</strong>
                <div className="hint" style={{ marginTop: ".2rem" }}>
                  {formats
                    ? `${formats.handbook.join(", ")} · up to ${(
                        formats.max_bytes / 1048576
                      ).toFixed(0)} MB`
                    : "handbooks, steps, procedures"}
                </div>
              </>
            )}
          </label>

          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: "var(--sp-3)" }}
            disabled={uploading || busy || !file}
            onClick={doUpload}
          >
            {uploading ? "Uploading…" : "Upload"}
          </button>
          <Message kind={uploadMsg?.kind}>{uploadMsg?.text}</Message>
        </div>

        <div style={{ overflowY: "auto", minHeight: 0, flex: "1 1 auto" }}>
          {!sources.length ? (
            <EmptyState>
              Nothing uploaded yet. Handbooks describe how a task is done, step
              by step.
            </EmptyState>
          ) : (
            <ul className="library">
              {sources.map((s) => (
                <li key={s.source_id}>
                  <button
                    type="button"
                    className="library-item"
                    aria-current={s.source_id === selectedId}
                    onClick={() => setSelectedId(s.source_id)}
                  >
                    <span className="library-top">
                      <span className="library-name truncate">{s.filename}</span>
                      {s.status === "failed" ? (
                        <span className="badge tone-R3">failed</span>
                      ) : s.pending_count ? (
                        <span className="badge tone-R2">
                          {s.pending_count} to review
                        </span>
                      ) : s.published_count ? (
                        <span className="badge tone-R0">live</span>
                      ) : null}
                    </span>
                    <span className="library-meta">
                      {(s.procedures || []).length} procedure
                      {(s.procedures || []).length === 1 ? "" : "s"} ·{" "}
                      {s.file_format} · {shortTime(s.uploaded_at).slice(0, 10)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      {/* ------------------- right: viewer or access ------------------- */}
      <Panel
        title={
          mode === "access"
            ? "Access credentials"
            : selected
            ? selected.filename
            : "Review & publish"
        }
        actions={
          <div className="tabs" role="group" aria-label="Section">
            <button
              type="button"
              className="tab"
              aria-selected={mode === "handbooks"}
              onClick={() => setMode("handbooks")}
            >
              Handbook
            </button>
            <button
              type="button"
              className="tab"
              aria-selected={mode === "access"}
              onClick={() => setMode("access")}
            >
              Access
            </button>
          </div>
        }
      >
        {mode === "access" ? (
          <CredentialsPanel busy={busy} />
        ) : selected ? (
          <HandbookDetail
            source={selected}
            content={content}
            detail={detail}
            busy={busy}
            actionMsg={actionMsg}
            onPublish={(id) =>
              act(() => api.publishDraft(id, "published from the handbook view"), "Published.")
            }
            onReject={(id) => {
              const note = prompt("Why are you rejecting this procedure?");
              if (note === null) return;
              act(() => api.rejectDraft(id, note), "Rejected.");
            }}
            onDelete={() => {
              if (
                !confirm(
                  `Remove "${selected.filename}"?\n\n` +
                    "This deletes the file, its draft procedures, and any of its " +
                    "procedures that are currently live."
                )
              )
                return;
              act(async () => {
                const r = await api.deleteSource(selected.source_id);
                setSelectedId(null);
                return r;
              }, "Removed.");
            }}
          />
        ) : (
          <ReviewExplainer />
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------- handbook detail + review */

function HandbookDetail({
  source,
  content,
  detail,
  busy,
  actionMsg,
  onPublish,
  onReject,
  onDelete,
}) {
  const procedures = source.procedures || [];
  const pending = procedures.filter((p) => p.status === "draft");
  const published = procedures.filter((p) => p.status === "published");
  const rejected = procedures.filter((p) => p.status === "rejected");

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <dl className="kv" style={{ flex: "1 1 auto" }}>
          <dt>Uploaded by</dt>
          <dd>{source.uploaded_by}</dd>
          <dt>When</dt>
          <dd>{shortTime(source.uploaded_at)}</dd>
          <dt>Procedures</dt>
          <dd>
            {published.length} live · {pending.length} awaiting review
            {rejected.length ? ` · ${rejected.length} rejected` : ""}
          </dd>
        </dl>
        <button
          type="button"
          className="btn btn-danger btn-sm"
          disabled={busy}
          onClick={onDelete}
        >
          Remove file
        </button>
      </div>

      <Message kind={actionMsg?.kind}>{actionMsg?.text}</Message>

      {(source.warnings || []).length ? (
        <Callout kind="warn" title="Warnings from reading this file">
          <ul>
            {source.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      {(source.errors || []).length ? (
        <Callout kind="danger" title="This file could not be read">
          <ul>
            {source.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      {/* --- procedures awaiting a decision --- */}
      {pending.length ? (
        <>
          <h3>Waiting for your decision</h3>
          <p className="hint" style={{ marginBottom: "var(--sp-3)" }}>
            These are not active. Publishing adds them to the live rule set;
            rejecting leaves a record of why.
          </p>
          {pending.map((p) => (
            <DraftCard
              key={p.draft_id}
              draft={detail[p.draft_id] || p}
              busy={busy}
              onPublish={() => onPublish(p.draft_id)}
              onReject={() => onReject(p.draft_id)}
            />
          ))}
        </>
      ) : null}

      {published.length ? (
        <>
          <h3>Live now</h3>
          <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
            {published.map((p) => (
              <li key={p.draft_id} className="row">
                <span className="badge tone-R0">live</span>
                <code>{p.procedure_id}</code>
                <span className="hint">{p.name}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {/* --- the file itself --- */}
      <h3>File contents</h3>
      {content?.available ? (
        <pre className="file-view">{content.content}</pre>
      ) : (
        <p className="hint">{content?.note || "Loading…"}</p>
      )}
    </>
  );
}

/* -------------------------------------------------------------- draft card */

function DraftCard({ draft, onPublish, onReject, busy }) {
  const effect =
    draft.effect || { steps: 0, agent_steps: 0, human_steps: 0, breakdown: [] };
  const blocked = (draft.validation_errors || []).length > 0;

  return (
    <div className="draft-card">
      <div className="draft-head">
        <div style={{ minWidth: 0 }}>
          <strong>{draft.name}</strong>
          <div className="hint">
            <code>{draft.procedure_id}</code>
            {draft.conflicts_with_existing ? (
              <>
                {" · "}
                <span className="badge tone-R2">replaces an existing one</span>
              </>
            ) : null}
          </div>
        </div>
        <span className="row">
          <span className="badge tone-agent">{effect.agent_steps} agent</span>
          <span className="badge tone-neutral">{effect.human_steps} you</span>
        </span>
      </div>

      {blocked ? (
        <Callout kind="danger" title="Cannot publish until fixed">
          <ul>
            {draft.validation_errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      {(draft.risk_warnings || []).length ? (
        <Callout kind="warn" title="Read before publishing">
          <ul>
            {draft.risk_warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Callout>
      ) : null}

      {effect.breakdown.length ? (
        <table className="table" style={{ marginTop: "var(--sp-3)" }}>
          <thead>
            <tr>
              <th>#</th>
              <th>Action</th>
              <th>Risk</th>
              <th>Who</th>
              <th>Step</th>
            </tr>
          </thead>
          <tbody>
            {effect.breakdown.map((b) => (
              <tr key={b.sequence}>
                <td>{b.sequence}</td>
                <td>
                  <code>{b.action}</code>
                </td>
                <td>
                  <RiskBadge code={b.risk} />
                </td>
                <td>{b.owner === "ai" ? "agent" : "you"}</td>
                <td className="hint">{b.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <div className="row" style={{ marginTop: "var(--sp-3)" }}>
        <button
          type="button"
          className="btn btn-ok"
          disabled={blocked || busy}
          onClick={onPublish}
        >
          Publish
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onReject}>
          Reject
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------- review/publish primer */

function ReviewExplainer() {
  return (
    <>
      <Callout kind="info">
        Select a handbook on the left to read it and decide what goes live.
      </Callout>

      <h3>How review and publish works</h3>
      <ol className="flow">
        <li>
          <strong>You upload a handbook</strong>
          <p className="hint">
            The file is read and split into one procedure per heading. Nothing
            about the system's behaviour changes at this point.
          </p>
        </li>
        <li>
          <strong>Each procedure becomes a draft</strong>
          <p className="hint">
            Drafts are never used for matching or execution. They exist only to
            be reviewed, so a bad file cannot affect live work.
          </p>
        </li>
        <li>
          <strong>The system classifies every step</strong>
          <p className="hint">
            Each step gets a risk level and an owner — agent or person. Read-only
            checks can go to the agent; anything whose safety cannot be proven
            stays with a person by default.
          </p>
        </li>
        <li>
          <strong>You read the split, then publish or reject</strong>
          <p className="hint">
            Publishing adds the procedure to the live rule set, so incoming
            tickets can match it. Rejecting records the reason. Either way the
            decision is attributed to you.
          </p>
        </li>
        <li>
          <strong>Publishing is still not permission to act</strong>
          <p className="hint">
            A published procedure only means the system knows the steps. Every
            run still needs approval before the agent does anything, and
            high-risk steps remain yours.
          </p>
        </li>
      </ol>

      <Callout kind="warn" title="Two things publishing checks for you">
        <ul>
          <li>
            A procedure that cannot work — a missing parameter, an unknown
            action — is blocked from publishing and tells you why.
          </li>
          <li>
            A file that claims a dangerous step is low-risk gets flagged, because
            that is the one way an upload could try to widen what the agent may
            do.
          </li>
        </ul>
      </Callout>
    </>
  );
}
