import { InfoPage } from "../../components/site/InfoPage";
import { rupees } from "../../lib/format";
import { PRICES, TRIAL } from "../../lib/site";

export function Terms() {
  return (
    <InfoPage title="Terms" updated="22 October 2026"
              intro="The rules for using BolDoc. Short, because the service is simple: you call, it writes.">
      <h2>The service</h2>
      <p>BolDoc writes documents from what you say on a call, and sends you a link to them. The writing is done by
        AI. Read every document before you use it: you are responsible for what you send or sign.</p>

      <h2>What you may not use it for</h2>
      <ul>
        <li>Forged or misleading documents: fake certificates, receipts, rent agreements, letterheads, government or
          legal documents you aren't entitled to issue.</li>
        <li>Anything illegal, defamatory, or meant to harass, threaten or deceive someone.</li>
        <li>Automated calls, or calls meant to run up costs.</li>
      </ul>
      <p>BolDoc may refuse such requests, end the call, or suspend the number.</p>

      <h2>Free trial and your balance</h2>
      <ul>
        <li>The first {TRIAL.calls} calls from a number are free (up to {TRIAL.minutes} minutes each, 2 new documents
          per call and 3 changes per document). A call counts once it lasts 20 seconds or makes a document.</li>
        <li>Signing up with your number adds {rupees(TRIAL.signupCredits)} to your balance, once per number.</li>
        <li>Your balance pays for: a call minute ₹{PRICES.minute}, a new document ₹{PRICES.document}, a change to a
          document ₹{PRICES.edit}, and a big document (a report or a deck, written after the call)
          ₹{PRICES.bigDocument}. The free {rupees(TRIAL.signupCredits)} can't be taken out as cash.</li>
        <li>Prices can change; the price shown when you use your balance is the one that applies.</li>
      </ul>

      <h2>Your documents</h2>
      <p>What you dictate and the documents BolDoc writes are yours. We use them only to provide the service, as the{" "}
        <a href="/privacy">privacy page</a> describes.</p>

      <h2>Availability</h2>
      <p>We work to keep BolDoc available, but calls can be busy or the service can be down. When too many people are
        calling, BolDoc says so and asks you to call back.</p>

      <h2>Liability</h2>
      <p>BolDoc is provided as is. To the extent the law allows, we aren't liable for losses from relying on a document
        without checking it.</p>

      <h2>Law</h2>
      <p>These terms are governed by the laws of India.</p>
    </InfoPage>
  );
}
