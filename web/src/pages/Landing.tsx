import { useEffect } from "react";
import { Link } from "react-router-dom";

import { HandsetIcon, MicIcon } from "../components/icons";
import { CallDemo } from "../components/site/CallDemo";
import { SiteFooter } from "../components/site/SiteFooter";
import { SiteHeader } from "../components/site/SiteHeader";
import { useAuth } from "../lib/auth";
import { track } from "../lib/beacon";
import { rupees } from "../lib/format";
import {
  BIG_SAYING, DIAL_IN_DISPLAY, DIAL_IN_E164, LANGUAGES, PRICES, SIGNUP_DOCS, TRIAL, TRY_SAYING,
} from "../lib/site";
import s from "./Landing.module.css";

// The console's four, and two more scripts: a Hindi letter in Devanagari and a Tamil receipt
const EXAMPLES = [
  ...TRY_SAYING,
  { say: "बैंक मैनेजर को खाता बंद करने का पत्र, SBI, मेरे नाम से", becomes: "A letter to the bank, PDF", lang: "hi" },
  { say: "வீட்டு வாடகை ரசீது, மாதம் 15,000, அக்டோபர் மாதத்திற்கு", becomes: "A rent receipt in Tamil, PDF", lang: "ta" },
];

const STEPS = [
  { title: "Call, or talk in your browser",
    body: `Dial ${DIAL_IN_DISPLAY} from any phone, talk here on the website, or let BolDoc ring you.` },
  { title: "Say what you need",
    body: "In your language, or a mix. BolDoc asks for anything missing and reads names, dates and amounts back to you." },
  { title: "Say yes, get the document",
    body: "The link comes by SMS, or it's on your screen. To change something, just say it; every version is kept." },
];

const TRUST = [
  { title: "An AI assistant, said up front",
    body: "Every call starts by saying BolDoc is an AI assistant. It reads facts back before it writes." },
  { title: "No recordings", body: "Audio is never stored. The text of a call is kept for 30 days, then deleted." },
  { title: "Your documents",
    body: "Private links that aren't searchable. Every version stays in your account until you delete it." },
  { title: "In India", body: "Calls, speech recognition, the AI that writes and storage all run in Indian data centres." },
];

const FAQ = [
  { q: "Do I need to sign up?", a: `Not to call. Your first ${TRIAL.calls} calls from a number are free, up to ${TRIAL.minutes} minutes each, no sign-up. Sign up with your mobile number to get ${rupees(TRIAL.signupCredits)} free, talk in your browser, get a call from BolDoc, and see all your documents in one place.` },
  { q: "Does it work on any phone?", a: "Yes. It's an ordinary phone call to an Indian number, so any mobile or landline works. The link comes by SMS to the mobile you called from." },
  { q: "Which languages can I speak?", a: "Hindi, English and Hinglish, Bengali, Marathi, Telugu, Tamil, Gujarati, Kannada, Malayalam and Odia. You can switch mid-call; BolDoc answers in the language you speak." },
  { q: "Can BolDoc write long documents?", a: `Yes, with a free account: project reports, business plans, pitch decks and more. BolDoc asks a few questions, reads back the outline, how long it will take and the price (${rupees(PRICES.bigDocument)}), and writes it after you hang up. If it needs something more, it asks once: on the call, by calling you back if you agreed, or by SMS. The link comes by SMS when it's ready.` },
  { q: "How do I change a document?", a: "On the same call, just say the change: BolDoc says it back and makes it when you say yes. Later, call again and say which document and what to change. Every version is kept, and the same link opens the latest one." },
  { q: "What if the SMS doesn't arrive?", a: "Sign in on this website with your number: all your documents are there. Some networks delay messages from new senders; it usually arrives within a minute." },
  { q: "Is my call recorded?", a: "No audio is recorded. The text of the conversation is kept for 30 days so you can see what was said, then deleted." },
  { q: "Where is my data?", a: "In India. Calls, speech recognition, the AI that writes, and storage all run in Indian data centres." },
];

export function Landing() {
  const { me } = useAuth();
  useEffect(() => {
    document.title = "BolDoc: documents by phone call, in your language";
    track("visit");
  }, []);
  const tel = `tel:${DIAL_IN_E164}`;

  return (
    <div className={s.page}>
      <SiteHeader signedIn={Boolean(me)} />
      <main>
        {/* one thing to read, one thing to press: prices and "Call me" live further down and in the console */}
        <section className={s.hero} id="try" aria-labelledby="hero-title">
          <div className={s.heroCopy}>
            <h1 className={s.display} id="hero-title"><span className={s.bolo}>Bolo.</span> Document taiyaar.</h1>
            <p className={s.lede}>
              Call and say what you need, in Hindi, English or any Indian language. BolDoc writes it while you talk,
              and the link comes by SMS.
            </p>
            <div className={s.ctas}>
              <a href={tel} className={s.call} onClick={() => track("cta_tel")}>
                <HandsetIcon size={20} /><span className={s.mono}>{DIAL_IN_DISPLAY}</span>
              </a>
              <Link to="/app/call" className={s.browser} onClick={() => track("cta_browser")}>
                <MicIcon size={20} />Talk in browser
              </Link>
            </div>
            <p className={s.ctaNote}>
              Your first {TRIAL.calls} calls are free, no sign-up. In the browser, sign in and
              get {rupees(TRIAL.signupCredits)} free.
            </p>
          </div>
          <CallDemo />
        </section>

        <section className={s.how} id="how" aria-labelledby="how-title">
          <div className={s.inner}>
            <div className={s.sectionHead}>
              <h2 className={s.sectionTitle} id="how-title">A call is all it takes.</h2>
              <p className={s.sectionLede}>
                No app, no forms. BolDoc listens, asks what's missing, and writes only after you say yes.
              </p>
            </div>
            <ol className={s.steps}>
              {STEPS.map((st, i) => (
                <li key={st.title} className={s.step}>
                  <span className={s.stepN} data-first={i === 0} aria-hidden="true">{i + 1}</span>
                  <h3 className={s.stepTitle}>{st.title}</h3>
                  <p>{st.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className={s.section} id="examples" aria-labelledby="ex-title">
          <div className={s.inner}>
            <div className={s.sectionHead}>
              <h2 className={s.sectionTitle} id="ex-title">Say it the way you'd say it.</h2>
              <p className={s.sectionLede}>
                Letters, invoices and sheets while you're on the call; project reports and decks after it.
              </p>
            </div>
            <ul className={s.chips}>
              {EXAMPLES.map((ex) => (
                <li key={ex.say} className={s.chip}>
                  <span className={s.quote} lang={ex.lang}>“{ex.say}”</span>
                  <span className={s.becomes}><i aria-hidden="true" />{ex.becomes}</span>
                </li>
              ))}
              <li className={`${s.chip} ${s.chipWide}`}>
                <span className={s.quote} lang={BIG_SAYING.lang}>“{BIG_SAYING.say}”</span>
                <span className={s.becomes}><i aria-hidden="true" />{BIG_SAYING.becomes}, with a free account</span>
              </li>
            </ul>

            <h3 className={s.subTitle}>What comes back</h3>
            <div className={s.samples}>
              <article className={s.sample}>
                <h4 className={s.sampleTitle}>Leave Application</h4>
                <p className={s.sampleMuted}>To the HR Manager, Exma Technologies</p>
                <p>I am writing to request leave from 26 October to 27 October 2026 to attend my sister's wedding in
                  Jaipur. I will resume my duties on 28 October.</p>
                <p className={s.sampleFoot}><span>PDF and Word</span><span>English</span></p>
              </article>
              <article className={s.sample}>
                <h4 className={s.sampleTitle}>Tax Invoice, Sharma Traders</h4>
                <table className={s.sampleTable}>
                  <thead><tr><th scope="col">Item</th><th scope="col">Amount</th></tr></thead>
                  <tbody>
                    <tr><td>Office chairs, 4</td><td>₹12,000.00</td></tr>
                    <tr><td>CGST 9%</td><td>₹1,080.00</td></tr>
                    <tr><td>SGST 9%</td><td>₹1,080.00</td></tr>
                  </tbody>
                  <tfoot><tr><td>Total</td><td>₹14,160.00</td></tr></tfoot>
                </table>
                <p className={s.sampleFoot}><span>PDF and Excel</span><span>English</span></p>
              </article>
              <article className={s.sample} lang="hi">
                <h4 className={s.sampleTitle}>सोसाइटी बैठक की सूचना</h4>
                <p className={s.sampleMuted}>सभी सदस्यों के लिए</p>
                <p>रविवार, 26 अक्टूबर को शाम 6 बजे क्लब हाउस में बैठक होगी। विषय: बजट, मरम्मत और दिवाली की तैयारी।</p>
                <p className={s.sampleFoot} lang="en"><span>PDF</span><span lang="hi">हिन्दी</span></p>
              </article>
            </div>
          </div>
        </section>

        <section className={`${s.section} ${s.band}`} id="languages" aria-labelledby="lang-title">
          <div className={s.inner}>
            <div className={s.sectionHead}>
              <h2 className={s.sectionTitle} id="lang-title">Speak your language. Switch any time.</h2>
              <p className={s.sectionLede}>
                BolDoc answers in the language you speak, and writes the document in the one you ask for.
              </p>
            </div>
            <ul className={s.langs}>
              {LANGUAGES.map((l) => (
                <li key={l.code} className={s.lang} lang={l.code === "od-IN" ? "or-IN" : l.code}>
                  <span className={s.native}>{l.native}</span>
                  <span className={s.langName}>{l.name}</span>
                </li>
              ))}
              <li className={s.lang} lang="en-IN">
                <span className={s.native}>Hinglish</span>
                <span className={s.langName}>Hindi and English, mixed</span>
              </li>
            </ul>
          </div>
        </section>

        <section className={s.section} id="pricing" aria-labelledby="price-title">
          <div className={s.inner}>
            <div className={s.sectionHead}>
              <h2 className={s.sectionTitle} id="price-title">Try it free. Pay only for what you use.</h2>
            </div>
            <div className={s.plans}>
              <article className={s.plan}>
                <h3 className={s.planName}>Free trial</h3>
                <p className={s.planBig}>{TRIAL.calls} calls</p>
                <p className={s.planBody}>No sign-up. Up to {TRIAL.minutes} minutes and {TRIAL.docsPerCall} documents a
                  call; the link comes by SMS to the number you called from.</p>
                <a href={tel} className={`${s.btn} ${s.btnLine}`} onClick={() => track("cta_tel")}>
                  Call {DIAL_IN_DISPLAY}
                </a>
              </article>
              <article className={s.plan} data-main>
                <h3 className={s.planName}>Free account</h3>
                <p className={s.planBig}><span className={s.rupee}>₹</span>{TRIAL.signupCredits.toLocaleString("en-IN")} free</p>
                <p className={s.planBody}>About {SIGNUP_DOCS} documents with short calls. Talk in your browser or get a call from
                  BolDoc, and keep every document in one place.</p>
                <Link to="/sign-in" className={`${s.btn} ${s.btnInk}`} onClick={() => track("signin_start")}>
                  Sign up free
                </Link>
              </article>
              <article className={s.plan}>
                <h3 className={s.planName}>Pay as you go</h3>
                <ul className={s.priceList}>
                  <li><span>A minute on a call</span><b>₹{PRICES.minute}</b></li>
                  <li><span>A new document</span><b>₹{PRICES.document}</b></li>
                  <li><span>A change to a document</span><b>₹{PRICES.edit}</b></li>
                  <li><span>A big document: a report or a deck</span><b>₹{PRICES.bigDocument}</b></li>
                </ul>
                <p className={s.soon}>Undoing a change is free. Adding money with UPI is coming soon.</p>
              </article>
            </div>
          </div>
        </section>

        <section className={`${s.section} ${s.band}`} id="privacy" aria-labelledby="trust-title">
          <div className={s.inner}>
            <h2 className={s.sectionTitle} id="trust-title">Your words stay yours.</h2>
            <ul className={s.trust}>
              {TRUST.map((t) => (
                <li key={t.title}>
                  <i aria-hidden="true" />
                  <h3>{t.title}</h3>
                  <p>{t.body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className={s.section} id="faq" aria-labelledby="faq-title">
          <div className={`${s.inner} ${s.faqWrap}`}>
            <h2 className={s.sectionTitle} id="faq-title">Questions</h2>
            <div className={s.faq}>
              {FAQ.map((f) => (
                <details key={f.q} className={s.faqItem}>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className={s.final} aria-labelledby="final-title">
          <h2 className={s.finalTitle} id="final-title"><span className={s.bolo}>Bolo.</span> Document taiyaar.</h2>
          <a href={tel} className={s.finalNumber} onClick={() => track("cta_tel")}>
            <HandsetIcon size={22} />{DIAL_IN_DISPLAY}
          </a>
          <p className={s.finalNote}>Your first {TRIAL.calls} calls are free. No app, no sign-up.</p>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
