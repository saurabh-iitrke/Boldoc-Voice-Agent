import { InfoPage } from "../../components/site/InfoPage";

export function Privacy() {
  return (
    <InfoPage title="Privacy" updated="22 October 2026"
              intro="What BolDoc keeps when you call it or use this website, why, where, for how long, and how to have it removed.">
      <h2>In short</h2>
      <ul>
        <li>Calls are answered by an AI assistant. Audio is never recorded or stored.</li>
        <li>The text of the conversation is kept for 30 days so you can see what was said, then deleted. You can
          choose not to keep it at all (My details, after you sign in).</li>
        <li>Everything runs in India: calls, speech recognition, the AI that writes, and storage.</li>
        <li>Your document links are private: not searchable, not shared with other sites, and they expire after 7 days.</li>
        <li>We don't sell or share your data for advertising, and the website uses no third-party trackers.</li>
      </ul>

      <h2>What we keep</h2>
      <table>
        <thead><tr><th>What</th><th>Why</th><th>How long</th></tr></thead>
        <tbody>
          <tr><td>Your phone number (the number you call from)</td><td>To know whose documents these are, send the link by SMS, run the free trial</td><td>While you use BolDoc</td></tr>
          <tr><td>The conversation's text</td><td>To write the document you asked for, and so you can check what was said</td><td>30 days, or not at all if you choose</td></tr>
          <tr><td>Your documents and their versions</td><td>They're yours: to open, download and change</td><td>While you have an account; 30 days if you never sign in</td></tr>
          <tr><td>Call details: time, length, cost, how it ended (no words)</td><td>Billing, the free trial's limits, preventing abuse, service quality</td><td>1 year</td></tr>
          <tr><td>SMS delivery status</td><td>To tell you whether the link arrived</td><td>1 year</td></tr>
          <tr><td>"My details" (name, designation, organisation, address)</td><td>Only if you add them: so documents can include them</td><td>Until you remove them</td></tr>
          <tr><td>A sign-in cookie</td><td>To keep you signed in (we store only a fingerprint of it)</td><td>30 days</td></tr>
          <tr><td>Anonymous page counts (for example "a visit", "a call button tapped")</td><td>To see whether the website works</td><td>Counts only: no cookie, no identifier, no IP address</td></tr>
        </tbody>
      </table>

      <h2>Who processes it for us</h2>
      <ul>
        <li><strong>Plivo</strong>: telephone calls and SMS. Calls to our Indian number are handled in India.</li>
        <li><strong>Sarvam AI</strong>: speech recognition, the voice you hear, and the language model; hosted in India.</li>
        <li><strong>Amazon Web Services, Mumbai</strong>: our servers and database, and the language model that
          writes documents (and answers calls when the line is busy), on Amazon Bedrock in Mumbai.</li>
      </ul>
      <p>They process data only to provide these services to us.</p>

      <h2>Your rights</h2>
      <p>Under India's Digital Personal Data Protection Act, 2023, you can ask to see the data we hold about you,
        correct it, or have it erased, and you can complain to us or to the Data Protection Board of India. Write to
        us from the phone number or email on your account; we reply within 30 days.</p>
      <p>BolDoc is meant for people aged 18 and over.</p>

      <h2>Security</h2>
      <p>Connections are encrypted. Sign-in codes, session cookies and document links are stored only as
        fingerprints (hashes), so a copy of our database wouldn't let anyone sign in or open your documents.</p>

      <h2>Contact and grievances</h2>
      <p>Grievance officer: <a href="mailto:grievance@boldoc.in">grievance@boldoc.in</a>. Support:{" "}
        <a href="mailto:support@boldoc.in">support@boldoc.in</a>.</p>
    </InfoPage>
  );
}
