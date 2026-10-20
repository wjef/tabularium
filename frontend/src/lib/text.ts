/** Lower case without accents ("Łódź" → "lodz"), like the server's `normalize`. */
export function fold(text: string): string {
  return text
    .toLowerCase()
    .replace(/ł/g, "l")
    .replace(/ß/g, "ss")
    .replace(/đ/g, "d")
    .replace(/ø/g, "o")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

/** How well `text` matches the typed `query` (every word must appear); 0 = no match. */
export function matchScore(text: string, query: string): number {
  const words = fold(query).split(/[\s/]+/).filter(Boolean);
  if (!words.length) return 0;
  const target = fold(text);
  let score = 0;
  for (const word of words) {
    const at = target.indexOf(word);
    if (at < 0) return 0;
    const wordStart = at === 0 || /[\s/.\-_]/.test(target[at - 1]);
    score += wordStart ? 2 : 1;
  }
  return score;
}

/** Key under which two names count as the same (case, accents and spacing ignored), as on the server. */
export function nameKey(name: string): string {
  return fold(name).split(/\s+/).filter(Boolean).join(" ");
}
