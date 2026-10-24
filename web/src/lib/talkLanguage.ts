import { useState } from "react";

import { useAuth } from "./auth";
import { LANGUAGES } from "./site";

export const TALK_DEFAULT = "en-IN";

/** English first (the default), then Hinglish, then the rest in the landing's order. "hi-IN" is BolDoc's Hindi and
   English voice (the talker's "hinglish" family), so the picker calls it Hinglish.
*/
export const TALK_LANGUAGES = [
  ...LANGUAGES.filter((l) => l.code === TALK_DEFAULT),
  ...LANGUAGES.filter((l) => l.code !== TALK_DEFAULT).map((l) => (l.code === "hi-IN" ? { ...l, name: "Hinglish" } : l)),
];

/** The language BolDoc starts a website call in ("BolDoc talks in": the browser call and "Call me"): the one the
 * account's last call ended in, however they called, else English. Returns [shown, pick, picked]: `picked` is set only
 * when they choose one for this call, and only a pick is sent, so a call nobody picked for starts where their last call
 * ended and follows them from there.
 */
export function useTalkLanguage(): [string, (code: string) => void, string | undefined] {
  const { me } = useAuth();
  const [picked, setPicked] = useState<string>();
  const remembered = TALK_LANGUAGES.find((l) => l.code === me?.call_lang)?.code ?? TALK_DEFAULT;
  return [picked ?? remembered, setPicked, picked];
}
