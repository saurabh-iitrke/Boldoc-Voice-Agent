import { Link } from "react-router-dom";

import { InfoPage } from "../../components/site/InfoPage";
import { useDialIn } from "../../lib/dialIn";
import { rupees } from "../../lib/format";
import { DIAL_IN_DISPLAY, LANGUAGES, TRIAL } from "../../lib/site";

export function Help() {
  const line = useDialIn();  // signed in: the account's own line; otherwise the public number
  return (
    <InfoPage title="Help" intro={<>Everything starts with a call to <a href={`tel:${line.e164}`}>{line.display}</a>.</>}>
      <h2>Making a document</h2>
      <ol>
        <li>Call {line.display} from your mobile.</li>
        <li>Say what you need, the way you'd tell a colleague: what kind of document, who it's for, the facts (names,
          dates, amounts), and the format (PDF, Word, Excel, PowerPoint).</li>
        <li>BolDoc asks for anything missing and reads names and numbers back. Correct it if it heard wrong.</li>
        <li>It writes while you talk, tells you when it's ready, and sends the link by SMS to the number you called from.</li>
      </ol>
      <blockquote>Try: "Mujhe HR ke liye leave application chahiye, 25 se 27 October, behen ki shaadi hai. PDF aur Word dono."</blockquote>

      <h2>Other ways to start</h2>
      <p>With a free account you can also <Link to="/app/call">talk to BolDoc in your browser</Link> (the document
        appears on your screen), or <Link to="/app?call=me">get a call from BolDoc</Link> on your own number.</p>

      <h2>Changing a document</h2>
      <p>On the same call, just say the change ("date 26 kar do", "add my phone number at the end"). Every change makes
        a new version; the same link always opens the latest one, and older versions stay available.</p>

      <h2>Languages</h2>
      <p>{LANGUAGES.map((l) => l.name).join(", ")}, and Hinglish. You can switch mid-call; BolDoc answers in the language
        you speak. Ask for the document in a different language if you like ("Hindi mein likhiye").</p>

      <h2>The SMS didn't arrive</h2>
      <ul>
        <li>Wait a minute: messages can come from an unfamiliar sender, and some networks hold them briefly.</li>
        <li><Link to="/sign-in">Sign in</Link> with the same number: all your documents are on your account page.</li>
        <li>A call from a landline or a hidden number can't get an SMS: call from your mobile instead.</li>
      </ul>

      <h2>Signing in</h2>
      <p>Enter your mobile number and the 6-digit code we text you. If the code doesn't come, choose "Verify by
        calling" and call {DIAL_IN_DISPLAY} from that number: BolDoc confirms and hangs up, and you're signed in.</p>

      <h2>Free trial and your balance</h2>
      <p>Your first {TRIAL.calls} calls from a number are free, up to {TRIAL.minutes} minutes each, no sign-up. Signing
        up puts {rupees(TRIAL.signupCredits)} in your balance, which pays for calls and documents; your account's Balance page
        shows what's left and the history. See <a href="/#pricing">pricing</a>.</p>

      <h2>Still stuck?</h2>
      <p>Write to <a href="mailto:support@boldoc.in">support@boldoc.in</a> from your number with the time of your call.</p>
    </InfoPage>
  );
}
