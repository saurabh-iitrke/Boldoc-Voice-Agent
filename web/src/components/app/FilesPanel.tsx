import { useEffect, useId, useRef, useState } from "react";

import { ApiError, api } from "../../lib/api";
import type { FileItem } from "../../lib/types";
import { UploadIcon } from "../icons";
import s from "./FilesPanel.module.css";

const ACCEPT = [".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".jpg", ".jpeg", ".png"];
const READABLE = "BolDoc reads PDF, Word, Excel, PowerPoint, CSV and text files, and photos (JPG, PNG).";
const MAX_BYTES = 20_000_000;
const KIND: Record<string, string> = { pdf: "PDF", docx: "Word", xlsx: "Excel", pptx: "PowerPoint", csv: "CSV",
  txt: "Text", image: "Photo" };

function size(bytes: number): string {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

function status(f: FileItem): string {
  if (f.status === "reading") return f.kind === "image" ? "Reading the photo…" : "Reading it…";
  if (f.status === "failed") return `Couldn't read it: ${f.error ?? "try another copy"}`;
  const pages = f.pages ? `${f.pages} ${f.kind === "xlsx" ? (f.pages === 1 ? "sheet" : "sheets")
    : f.kind === "pptx" ? (f.pages === 1 ? "slide" : "slides") : (f.pages === 1 ? "page" : "pages")}` : "";
  return f.read_by === "vision" ? `Read from the ${f.kind === "image" ? "photo" : "scan"}${pages ? `, ${pages}` : ""}`
    : `Read${pages ? `, ${pages}` : ""}`;
}

/** Files for BolDoc to use. On the home page they wait for the owner's next big document (the next report, plan or
 * deck asked for on a call takes them); on a long document's page they're for it. Only their text is kept, and the
 * writer reads it as the owner's own facts. */
export function FilesPanel({ docId, variant, writing = false }: {
  docId?: string; variant: "home" | "doc"; writing?: boolean;
}) {
  const [files, setFiles] = useState<FileItem[] | null>(null);
  const [error, setError] = useState<string>();
  const [adding, setAdding] = useState(0);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();

  const load = () => api.files(docId).then(setFiles).catch(() => setFiles((f) => f ?? []));
  useEffect(() => { void load(); }, [docId]);  // eslint-disable-line react-hooks/exhaustive-deps
  // while a file is being read, look again now and then
  useEffect(() => {
    if (!files?.some((f) => f.status === "reading")) return;
    const t = window.setTimeout(() => void load(), 2500);
    return () => window.clearTimeout(t);
  }, [files]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function add(chosen: File[]) {
    setError(undefined);
    for (const f of chosen) {
      if (!ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext))) {
        setError(`${f.name}: ${READABLE}`);
        continue;
      }
      if (f.size > MAX_BYTES) {
        setError(`${f.name} is over 20 MB. Add a smaller one, or a few pages of it.`);
        continue;
      }
      setAdding((n) => n + 1);
      try {
        const item = await api.addFile(f, docId);
        setFiles((prev) => [...(prev ?? []), item]);
      } catch (e) {
        setError(e instanceof ApiError ? `${f.name}: ${e.message}` : `${f.name} couldn't be added. Try again.`);
      } finally {
        setAdding((n) => n - 1);
      }
    }
  }

  async function remove(f: FileItem) {
    try {
      await api.removeFile(f.id);
      setFiles((prev) => prev?.filter((x) => x.id !== f.id) ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That file couldn't be removed. Try again.");
    }
  }

  const lead = variant === "home"
    ? "An estimate, a price list, last year's report or a photo of a bill. BolDoc reads them and uses them in the next report, plan or deck you ask for on a call."
    : writing ? "BolDoc's writer reads these as your own facts: their figures go into this document."
      : "BolDoc uses these when you call to change this document.";

  return (
    <section className={variant === "home" ? s.home : s.doc} aria-labelledby={titleId}>
      <div className={s.head}>
        <h2 id={titleId} className={variant === "home" ? s.titleHome : s.titleDoc}>
          {variant === "home" ? "Files for your next document" : "Files for this document"}
        </h2>
        <p className={s.lead}>{lead}</p>
      </div>

      <div className={s.drop} data-over={over || undefined}
           onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
           onDrop={(e) => { e.preventDefault(); setOver(false); void add(Array.from(e.dataTransfer.files)); }}>
        <input ref={input} type="file" multiple accept={ACCEPT.join(",")} className={s.input} tabIndex={-1}
               aria-hidden="true" onChange={(e) => { void add(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <button type="button" className={s.add} onClick={() => input.current?.click()}>
          <UploadIcon size={17} /> Add files
        </button>
        <span className={s.hint}>or drop them here. Up to 10 files, 20 MB in all.</span>
      </div>

      {files && files.length > 0 && (
        <ul className={s.list}>
          {files.map((f) => (
            <li key={f.id} className={s.item} data-status={f.status}>
              <span className={s.kind}>{KIND[f.kind] ?? f.kind.toUpperCase()}</span>
              <div className={s.main}>
                <p className={s.name} title={f.name}>{f.name}</p>
                <p className={s.status}>
                  {f.status === "reading" && <span className={s.dot} aria-hidden="true" />}
                  {status(f)}<span className={s.size}>{size(f.size)}</span>
                </p>
              </div>
              <button type="button" className={s.remove} onClick={() => void remove(f)}
                      aria-label={`Remove ${f.name}`}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {adding > 0 && <p className={s.adding} role="status"><span className={s.dot} aria-hidden="true" />Adding…</p>}
      {error && <p className={s.error} role="alert">{error}</p>}
    </section>
  );
}
