import { useEffect, useRef } from "react";

import { MIC } from "./mic";

/**
 * Drives the margin line from the caller's microphone without re-rendering React:
 * the RMS level is written to the element's `--level` custom property every animation frame.
 * `mode`: "mic" = real microphone, "synthetic" = demo (a speech-like envelope while `speaking`), "off".
 */
export function useVoiceLevel(mode: "mic" | "synthetic" | "off", speaking: boolean) {
  const ref = useRef<HTMLDivElement | null>(null);
  const speakingRef = useRef(speaking);
  speakingRef.current = speaking;

  useEffect(() => {
    const el = ref.current;
    if (!el || mode === "off") {
      el?.style.setProperty("--level", "0");
      return;
    }
    let raf = 0;
    let smooth = 0;
    let stop = () => {};
    const set = (v: number) => {
      smooth = smooth * 0.75 + v * 0.25; // attack/decay smoothing so the line breathes instead of flickers
      el.style.setProperty("--level", smooth.toFixed(3));
    };

    if (mode === "synthetic") {
      const t0 = performance.now();
      const tick = (t: number) => {
        const s = (t - t0) / 1000;
        const syllables = Math.max(0, Math.sin(s * 9.1) * 0.5 + Math.sin(s * 3.7 + 1) * 0.35 + 0.25);
        set(speakingRef.current ? Math.min(1, syllables) : 0);
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }

    let cancelled = false;
    navigator.mediaDevices?.getUserMedia({ audio: MIC })
      .then((stream) => {
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        const ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const buf = new Float32Array(analyser.fftSize);
        const tick = () => {
          analyser.getFloatTimeDomainData(buf);
          let sum = 0;
          for (const x of buf) sum += x * x;
          const rms = Math.sqrt(sum / buf.length);
          set(Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-8) + 55) / 40))); // -55 dB..-15 dB -> 0..1
          raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        stop = () => { stream.getTracks().forEach((t) => t.stop()); void ctx.close(); };
      })
      .catch(() => el.style.setProperty("--level", "0"));
    return () => { cancelled = true; cancelAnimationFrame(raf); stop(); };
  }, [mode]);

  return ref;
}
