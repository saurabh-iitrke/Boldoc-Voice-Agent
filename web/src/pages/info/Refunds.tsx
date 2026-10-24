import { InfoPage } from "../../components/site/InfoPage";
import { rupees } from "../../lib/format";
import { TRIAL } from "../../lib/site";

export function Refunds() {
  return (
    <InfoPage title="Refunds" updated="22 October 2026"
              intro={`BolDoc doesn't take payments yet: the free trial and the ${rupees(TRIAL.signupCredits)} at sign-up cost nothing.`}>
      <h2>When top-ups arrive</h2>
      <p>Adding money with UPI is coming. Before we take any payment, this page will say exactly how refunds
        work: money you added and didn't use, failed or duplicate payments, and how long a refund takes.</p>
      <h2>Something went wrong on a call?</h2>
      <p>If a call failed or a document didn't arrive and your balance was charged, write to{" "}
        <a href="mailto:support@boldoc.in">support@boldoc.in</a> from your number, with the time of the call. We put
        the money back for calls that didn't work.</p>
    </InfoPage>
  );
}
