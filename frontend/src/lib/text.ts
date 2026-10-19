/** Lower case without accents ("Łódź" → "lodz"), for forgiving matching. */
export function fold(text: string): string {
  return text
    .toLowerCase()
    .replace(/ł/g, "l")
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
