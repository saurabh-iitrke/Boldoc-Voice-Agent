import { diffWords } from "../lib/diffWords";
import { formatCell, parseNumber } from "../lib/format";
import type { Block, Doc } from "../lib/types";
import s from "./DocBlocks.module.css";

const META_ORDER: [string, string][] = [["date", "Date"], ["from", "From"], ["to", "To"], ["subject", "Subject"],
  ["ref", "Ref"]];

const plain = (b: Block | undefined) => (b && b.type === "paragraph" ? b.runs.map((r) => r.text).join("") : "");

/** The words a version changed, struck and inserted the way the landing demo marks them. */
function Marked({ before, after }: { before: string; after: string }) {
  if (before === after) return <>{after}</>;
  return (
    <>
      {diffWords(before, after).map((p, k) =>
        p.kind === "same" ? <span key={k}>{p.text}</span>
          : p.kind === "del" ? (p.text.trim() ? <del key={k} className={s.del}>{p.text}</del> : null)
            : <ins key={k} className={s.ins}>{p.text}</ins>)}
    </>
  );
}

/** Renders the canonical document model (packages/docengine) as the typeset fair copy. Given the version before it
 * (the call's live page), the words this version changed are marked in place. */
export function DocBlocks({ doc, changed = [], prev }: { doc: Doc; changed?: string[]; prev?: Doc }) {
  const rtl = doc.script === "Arab";
  return (
    <article className={s.doc} lang={doc.lang} dir={rtl ? "rtl" : "ltr"}>
      <h1 className={s.title}>{doc.title}</h1>
      {META_ORDER.some(([k]) => doc.meta[k]) && (
        <dl className={s.meta}>
          {META_ORDER.filter(([k]) => doc.meta[k]).map(([k, label]) => (
            <div key={k} className={k === "subject" ? s.subject : undefined}>
              <dt>{label}</dt>
              <dd>{prev && prev.meta[k] !== undefined ? <Marked before={prev.meta[k]!} after={doc.meta[k]!} />
                : doc.meta[k]}</dd>
            </div>
          ))}
        </dl>
      )}
      {doc.blocks.map((b) => {
        // a changed paragraph with a version before it shows its words marked; other changed blocks get the bar
        const was = prev && b.type === "paragraph" && changed.includes(b.id)
          ? prev.blocks.find((p) => p.id === b.id) : undefined;
        return (
          <div key={b.id} className={changed.includes(b.id) && !was ? s.changed : undefined} data-block={b.id}>
            {was ? <p className={s.p}><Marked before={plain(was)} after={plain(b)} /></p> : <BlockView b={b} />}
          </div>
        );
      })}
      {doc.assumptions.length > 0 && (
        <aside className={s.assumptions}>
          <p className={s.assumeTitle}>Please check before you send</p>
          <ul>{doc.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </aside>
      )}
    </article>
  );
}

function BlockView({ b }: { b: Block }) {
  switch (b.type) {
    case "heading": {
      const Tag = (["h2", "h3", "h4"] as const)[b.level - 1] ?? "h2";
      return <Tag className={s[`h${b.level}`]}>{b.text}</Tag>;
    }
    case "paragraph":
      return (
        <p className={s.p}>
          {b.runs.map((r, i) => {
            let el: React.ReactNode = r.text;
            if (r.bold) el = <strong>{el}</strong>;
            if (r.italic) el = <em>{el}</em>;
            if (r.underline) el = <u>{el}</u>;
            return <span key={i}>{el}</span>;
          })}
        </p>
      );
    case "list": {
      const Tag = b.ordered ? "ol" : "ul";
      return <Tag className={s.list}>{b.items.map((it, i) => <li key={i}>{it}</li>)}</Tag>;
    }
    case "table": {
      const numeric = b.columns.map((c) => c.type === "number" || c.type === "currency");
      const firstText = numeric.indexOf(false);
      return (
        <div className={s.tableWrap}>
          <table className={s.table}>
            {b.caption && <caption>{b.caption}</caption>}
            <thead><tr>{b.columns.map((c, i) => <th key={c.name} className={numeric[i] ? s.num : undefined} scope="col">{c.name}</th>)}</tr></thead>
            <tbody>
              {b.rows.map((row, r) => (
                <tr key={r}>{row.map((v, i) => (
                  <td key={i} className={numeric[i] ? s.num : undefined}>{formatCell(v, b.columns[i]?.type ?? "text")}</td>
                ))}</tr>
              ))}
            </tbody>
            {b.total_row && numeric.some(Boolean) && b.rows.length > 0 && (
              <tfoot><tr>{b.columns.map((c, i) => (
                <td key={c.name} className={numeric[i] ? s.num : undefined}>
                  {numeric[i] ? formatCell(b.rows.reduce((sum, row) => sum + (parseNumber(row[i] ?? null) ?? 0), 0), c.type)
                    : i === firstText ? "Total" : ""}
                </td>
              ))}</tr></tfoot>
            )}
          </table>
        </div>
      );
    }
    case "kv":
      return (
        <dl className={s.kv}>
          {b.pairs.filter((p) => p.length >= 2).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
      );
    case "signature":
      return (
        <div className={s.signature}>
          <p>{b.closing}</p>
          <p className={s.signName}>{b.name}</p>
          {b.designation && <p>{b.designation}</p>}
        </div>
      );
    case "page_break":
      return <hr className={s.pageBreak} />;
    case "slide":
      return (
        <section className={s.slide}>
          <h2 className={s.h1}>{b.title}</h2>
          <ul className={s.list}>{b.bullets.map((x, i) => <li key={i}>{x}</li>)}</ul>
          {b.notes && <p className={s.notes}>{b.notes}</p>}
        </section>
      );
  }
}
