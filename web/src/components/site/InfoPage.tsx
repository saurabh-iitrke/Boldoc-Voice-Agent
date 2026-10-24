import { useEffect, type ReactNode } from "react";

import { SiteFooter } from "./SiteFooter";
import { SiteHeader } from "./SiteHeader";
import s from "./InfoPage.module.css";

/** A reading page (help, privacy, terms): the site's header and footer around one comfortable column. */
export function InfoPage({ title, intro, updated, children }: {
  title: string; intro?: ReactNode; updated?: string; children: ReactNode;
}) {
  useEffect(() => { document.title = `${title} · BolDoc`; window.scrollTo(0, 0); }, [title]);
  return (
    <>
      <SiteHeader />
      <main className={s.page}>
        <header className={s.head}>
          <h1 className={s.title}>{title}</h1>
          {intro && <p className={s.intro}>{intro}</p>}
          {updated && <p className={s.updated}>Last updated {updated}</p>}
        </header>
        <div className={s.body}>{children}</div>
      </main>
      <SiteFooter />
    </>
  );
}
