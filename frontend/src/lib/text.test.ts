import { describe, expect, it } from "vitest";
import { fold, matchScore, nameKey } from "./text";

describe("text matching", () => {
  it("ignores case and Polish accents", () => {
    expect(fold("Łódź Żółć")).toBe("lodz zolc");
    expect(matchScore("Faktury / Łódź", "lodz")).toBeGreaterThan(0);
  });

  it("needs every word and prefers word starts", () => {
    expect(matchScore("home / utilities / electricity", "home elec")).toBe(4);
    expect(matchScore("home / utilities", "home gas")).toBe(0);
    expect(matchScore("invoice", "voice")).toBeLessThan(matchScore("voice mail", "voice"));
  });
});

describe("name keys", () => {
  it("treat spellings of one name as the same", () => {
    expect(nameKey("  Ewa   WIŚNIEWSKA ")).toBe(nameKey("ewa wiśniewska"));
    expect(nameKey("Urząd Łódź")).toBe(nameKey("urzad lodz"));
    expect(nameKey("Anna Nowak")).not.toBe(nameKey("Anna Nowakowa"));
  });
});
