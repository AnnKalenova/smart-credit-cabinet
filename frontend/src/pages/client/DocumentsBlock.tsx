import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, FileText, Paperclip, Sparkles, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { api, download } from "../../api/client";
import type { Application, DocumentInfo } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { fileSize } from "../../lib/format";
import { useToast } from "../../lib/toast";

export function useRefreshApp(appId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["application", appId] });
    void qc.invalidateQueries({ queryKey: ["applications"] });
    void qc.invalidateQueries({ queryKey: ["notifications"] });
  };
}

function UploadButtons({ app, docType, editable }: { app: Application; docType: string; editable: boolean }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  if (!editable) return null;

  const upload = async (file: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("doc_type", docType);
      fd.append("file", file);
      await api(`/api/v1/applications/${app.id}/documents`, { method: "POST", body: fd });
      toast.ok("Документ загружен");
      refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const demo = async () => {
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/documents/demo?doc_type=${docType}`, { method: "POST" });
      toast.ok("Демо-документ приложен");
      refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row-wrap">
      <input
        ref={input}
        type="file"
        hidden
        accept=".pdf,.jpg,.jpeg,.png,.txt,.doc,.docx,.xls,.xlsx"
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
      />
      <button className="btn btn-ghost btn-s" disabled={busy} onClick={() => input.current?.click()}>
        <Paperclip size={15} /> Загрузить файл
      </button>
      <button className="btn btn-ghost btn-s" disabled={busy} onClick={demo} title="Синтетический документ для проверки сценария">
        <Sparkles size={15} /> Демо-файл
      </button>
    </div>
  );
}

function DocRow({ app, doc, editable }: { app: Application; doc: DocumentInfo; editable: boolean }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const remove = async () => {
    try {
      await api(`/api/v1/applications/${app.id}/documents/${doc.id}`, { method: "DELETE" });
      refresh();
    } catch (e) {
      toast.error(e);
    }
  };
  return (
    <div className="row small" style={{ padding: "6px 0" }}>
      <FileText size={16} className="faint" />
      <button className="btn-link" onClick={() => download(`/api/v1/applications/${app.id}/documents/${doc.id}`, doc.file_name)}>
        {doc.file_name}
      </button>
      <span className="faint">{fileSize(doc.size_bytes)}</span>
      <span className="spacer" />
      {editable && (
        <button className="icon-btn" style={{ width: 30, height: 30, color: "var(--ink-3)" }} onClick={remove} aria-label="Удалить документ">
          <Trash2 size={15} />
        </button>
      )}
    </div>
  );
}

/** Обязательные документы (по правилам продукта) + любые дополнительные. */
export function DocumentsBlock({ app, editable, extraTypes = [] }: { app: Application; editable: boolean; extraTypes?: string[] }) {
  const { dict } = useAuth();
  const required = app.required_documents;
  const requiredTypes = new Set([...required.map((r) => r.doc_type), ...extraTypes]);
  const others = app.documents.filter((d) => !requiredTypes.has(d.doc_type));
  const types = [...required.map((r) => ({ doc_type: r.doc_type, label: r.label })), ...extraTypes.filter((t) => !required.some((r) => r.doc_type === t)).map((t) => ({ doc_type: t, label: dict?.doc_types[t] ?? t }))];

  return (
    <div className="stack">
      {types.length === 0 && <p className="small muted">Для этой заявки документы не нужны — банк оценит бизнес по оборотам на счёте.</p>}
      {types.map((r) => {
        const docs = app.documents.filter((d) => d.doc_type === r.doc_type);
        return (
          <div key={r.doc_type} className="panel-flat" style={{ padding: "12px 14px" }}>
            <div className="row">
              {docs.length ? <CircleCheck size={18} className="pos" /> : <span style={{ width: 18 }} />}
              <b className="small">{r.label}</b>
              <span className="spacer" />
              <UploadButtons app={app} docType={r.doc_type} editable={editable} />
            </div>
            {docs.map((d) => (
              <DocRow key={d.id} app={app} doc={d} editable={editable} />
            ))}
          </div>
        );
      })}
      {(others.length > 0 || editable) && (
        <div>
          <div className="row">
            <span className="small muted">Другие документы</span>
            <span className="spacer" />
            <UploadButtons app={app} docType="OTHER" editable={editable} />
          </div>
          {others.map((d) => (
            <DocRow key={d.id} app={app} doc={d} editable={editable} />
          ))}
        </div>
      )}
    </div>
  );
}
