import { useEffect, useState } from "react";

import { FORMAT_LABEL } from "../lib/format";
import type { VersionEntry } from "../lib/live";
import type { Delivery, Doc, Format } from "../lib/types";
import { DocBlocks } from "./DocBlocks";
import { FilePreview } from "./FilePreview";
import s from "./FairCopy.module.css";
import { DocIcon, DownloadIcon } from "./icons";
import { Stamp } from "./Stamp";

type Props = {
  docs: Record<number, Doc>;
  latest?: number;
  versions: VersionEntry[];
  deliveries: Delivery[];
  writing: boolean;      // the thinker is producing the next version right now
  stage?: string;        // e.g. "Writing the body"
  docId?: string;        // for downloads, once the document exists
  formats?: Format[];    // its formats (from the API), each one a download when it's ready
  canDownload?: boolean; // a real call (not the recorded one, not the mock)
};

/** The document BolDoc is writing, as its own card: a typeset page on a grey stage that follows the latest version
 * unless you pick another, the words a version changed marked in place, and its downloads once it's ready. */
export function FairCopy({ docs, latest, versions, deliveries, writing, stage, docId, formats = [],
  canDownload = false }: Props) {
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => { setPicked(null); }, [latest]); // a new version always brings you back to the latest

  const shownVersion = picked ?? latest;
  const doc = shownVersion ? docs[shownVersion] : undefined;
  const prev = shownVersion && picked === null ? docs[shownVersion - 1] : undefined;
  const entry = versions.find((v) => v.version === shownVersion);
  const delivery = [...deliveries].reverse().find((d) => d.version === shownVersion);
  const nextVersion = (latest ?? 0) + 1;
  const ready = !!doc && !writing;
  // On the live site, the written version as its file looks when downloaded (the PDF if there is one)
  const fileFmt = formats.includes("pdf") ? "pdf" : formats[0];
  const files = canDownload && ready && !!docId && !!fileFmt && !!shownVersion;

  return (
    <section className={s.card} aria-labelledby="doc-h">
      <header className={s.head}>
        <span className={s.icon} aria-hidden="true"><DocIcon size={18} /></span>
        <h2 id="doc-h" className={s.title}>{doc?.title ?? "Your document"}</h2>
        {writing ? (
          <span className={s.chipLive} role="status">
            <span className={s.pulse} aria-hidden="true" />
            {latest ? `Changing to version ${nextVersion}` : "Writing"}
          </span>
        ) : latest && versions.length <= 1 ? (
          <span className={s.chip}>Version {latest}</span>
        ) : null}
        {versions.length > 1 && (
          <div className={s.tabs} role="tablist" aria-label="Versions">
            {versions.map((v) => (
              <button key={v.version} type="button" role="tab" aria-selected={v.version === shownVersion}
                className={s.tab} onClick={() => setPicked(v.version === latest ? null : v.version)}>
                Version {v.version}
              </button>
            ))}
          </div>
        )}
        {formats.length > 0 && (
          <span className={s.downloads}>
            {formats.map((f) => (canDownload && ready && docId ? (
              <a key={f} className={s.dl} href={`/api/documents/${encodeURIComponent(docId)}/download/${f}?v=${shownVersion}`}
                download aria-label={`Download ${FORMAT_LABEL[f] ?? f}, version ${shownVersion}`}>
                <DownloadIcon size={15} />{FORMAT_LABEL[f] ?? f}
              </a>
            ) : (
              <button key={f} type="button" className={s.dl} disabled
                title={canDownload ? "Ready to download once it's written" : "Downloads open in your documents"}>
                <DownloadIcon size={15} />{FORMAT_LABEL[f] ?? f}
              </button>
            )))}
          </span>
        )}
      </header>
      <div className={s.stage}>
        <div className={`${s.sheet} ${files ? s.sheetFiles : ""}`}>
          {delivery && <div className={s.stamp}><Stamp delivery={delivery} /></div>}
          {picked !== null && picked !== latest && (
            <p className={s.older} role="status">
              You're looking at version {picked}.{" "}
              <button type="button" onClick={() => setPicked(null)}>Show version {latest}</button>
            </p>
          )}
          {doc ? (
            <>
              {entry && entry.version > 1 && entry.changes.length > 0 && (
                <p className={s.changes}><span className={s.changesLabel}>What changed:</span> {entry.changes.join("; ")}.</p>
              )}
              {files ? (
                <>
                  <FilePreview key={`${docId}@${shownVersion}@${fileFmt}`} fmt={fileFmt}
                    url={`/api/documents/${encodeURIComponent(docId)}/preview/${fileFmt}?v=${shownVersion}`}
                    label={`${doc.title}, version ${shownVersion}`} />
                  <div className="visually-hidden"><DocBlocks doc={doc} /></div>
                </>
              ) : (
                <DocBlocks doc={doc} prev={prev} changed={picked === null ? entry?.changedBlockIds : []} />
              )}
            </>
          ) : (
            <div className={s.blank}>
              {writing ? (
                <p className={s.writingFirst}>
                  <span className={s.nib} aria-hidden="true" />
                  {stage ? `${stage}…` : "Starting your document…"}
                </p>
              ) : (
                <>
                  <p className={s.blankLead}>Your document appears here as soon as BolDoc starts writing.</p>
                  <p className={s.blankHint}>Try saying: “Write a leave application to my manager for 25 to 27 October.”</p>
                </>
              )}
            </div>
          )}
          {doc && writing && (
            <p className={s.writingNext} role="status">
              <span className={s.nib} aria-hidden="true" />
              Writing version {nextVersion}{stage ? `: ${stage.charAt(0).toLowerCase()}${stage.slice(1)}` : ""}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
