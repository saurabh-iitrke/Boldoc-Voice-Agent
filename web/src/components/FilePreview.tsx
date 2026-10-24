import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

import s from "./FilePreview.module.css";

/** How a format is shown: the PDF itself, and Word, Excel and PowerPoint as the server lays them out (both drawn by
 * pdf.js, so the page looks exactly like the download); text formats as their text; HTML in a sandbox. */
export type PreviewKind = "pdf" | "text" | "html";
export const previewKind = (fmt: string): PreviewKind =>
  fmt === "html" ? "html" : ["md", "txt", "csv"].includes(fmt) ? "text" : "pdf";

type Props = { url: string; fmt: string; label: string; onFail?: () => void; firstPageOnly?: boolean };

export function FilePreview({ url, fmt, label, onFail, firstPageOnly = false }: Props) {
  const kind = previewKind(fmt);
  if (kind === "text") return <TextPreview url={url} label={label} onFail={onFail} />;
  if (kind === "html") return <iframe className={s.html} sandbox="" src={url} title={label} />;
  return <PdfPreview url={url} label={label} onFail={onFail} firstPageOnly={firstPageOnly} />;
}

type Size = { w: number; h: number };

function PdfPreview({ url, label, onFail, firstPageOnly }: Omit<Props, "fmt">) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<Size[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let destroyTask: (() => void) | null = null;  // ends the document and its worker
    setDoc(null);
    setSizes(null);
    setFailed(false);
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        const task = pdfjs.getDocument({ url, withCredentials: true });
        destroyTask = () => void task.destroy();
        const opened = await task.promise;
        const all: Size[] = [];
        for (let n = 1; n <= (firstPageOnly ? 1 : opened.numPages); n++) {
          const v = (await opened.getPage(n)).getViewport({ scale: 1 });
          all.push({ w: v.width, h: v.height });
        }
        if (cancelled) return;
        setDoc(opened);
        setSizes(all);
      } catch {
        if (!cancelled) {
          setFailed(true);
          onFail?.();
        }
      }
    })();
    return () => {
      cancelled = true;
      destroyTask?.();
    };
  }, [url, onFail, firstPageOnly]);

  if (failed) return <p className={s.note} role="status">The preview didn't open. Download the file to see it.</p>;
  if (!doc || !sizes) {
    return <div className={s.pages} aria-busy="true"><div className={`${s.page} ${s.waiting}`} /></div>;
  }
  return (
    <div className={s.pages} aria-label={label}>
      {sizes.map((size, i) => (
        <Page key={`${url}#${i}`} doc={doc} n={i + 1} of={sizes.length} size={size} />
      ))}
    </div>
  );
}

/** One page: drawn when it's near the screen, again if its width changes. */
function Page({ doc, n, of, size }: { doc: PDFDocumentProxy; n: number; of: number; size: Size }) {
  const holder = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(n === 1);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const seen = new IntersectionObserver((all) => { if (all[0]?.isIntersecting) setNear(true); },
      { rootMargin: "800px 0px" });
    const sized = new ResizeObserver((all) => { if (all[0]) setWidth(Math.round(all[0].contentRect.width)); });
    seen.observe(el);
    sized.observe(el);
    return () => { seen.disconnect(); sized.disconnect(); };
  }, []);

  useEffect(() => {
    if (!near || !width || !canvas.current) return;
    let cancelled = false;
    let render: { cancel: () => void } | null = null;
    (async () => {
      const page = await doc.getPage(n);
      if (cancelled || !canvas.current) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = page.getViewport({ scale: (width / size.w) * dpr });
      canvas.current.width = Math.floor(viewport.width);
      canvas.current.height = Math.floor(viewport.height);
      const task = page.render({ canvas: canvas.current, viewport });
      render = task;
      await task.promise.catch(() => undefined);  // a newer draw replaced it
    })();
    return () => { cancelled = true; render?.cancel(); };
  }, [doc, n, near, width, size.w]);

  return (
    <div ref={holder} className={s.page} style={{ aspectRatio: `${size.w} / ${size.h}` }}>
      <canvas ref={canvas} className={s.canvas} role="img" aria-label={`Page ${n} of ${of}`} />
    </div>
  );
}

function TextPreview({ url, label, onFail }: Omit<Props, "fmt">) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setText(null);
    setFailed(false);
    fetch(url, { credentials: "include" })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (!cancelled) setText(t); })
      .catch(() => { if (!cancelled) { setFailed(true); onFail?.(); } });
    return () => { cancelled = true; };
  }, [url, onFail]);
  if (failed) return <p className={s.note} role="status">The preview didn't open. Download the file to see it.</p>;
  return (
    <div className={s.pages} aria-label={label} aria-busy={text === null}>
      <pre className={`${s.page} ${s.text}`}>{text ?? ""}</pre>
    </div>
  );
}
