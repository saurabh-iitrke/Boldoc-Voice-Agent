/** Word-level diff of two strings: runs of unchanged, removed and added words (whitespace kept with the words).
 * The same longest-common-subsequence diff as the landing demo (components/site/CallDemo.tsx). */
export type DiffPart = { kind: "same" | "del" | "ins"; text: string };

export function diffWords(a: string, b: string): DiffPart[] {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/);
  const m = x.length, n = y.length;
  const lcs: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      lcs[i]![j] = x[i] === y[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
  const out: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text; else out.push({ kind, text });
  };
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (x[i] === y[j]) { push("same", x[i]!); i++; j++; }
    else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) push("del", x[i++]!);
    else push("ins", y[j++]!);
  }
  while (i < m) push("del", x[i++]!);
  while (j < n) push("ins", y[j++]!);
  return out;
}
