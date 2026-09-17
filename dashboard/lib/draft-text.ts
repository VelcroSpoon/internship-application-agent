import type { RoundDetail } from "./types";

/** Plain text for the clipboard: what I paste into the employer's own form. */
export function draftToText(round: RoundDetail): string {
  const bullets = round.bullets.map((b) => `- ${b.text}`).join("\n");
  return `${bullets}\n\n${round.cover_letter.trim()}\n`;
}
