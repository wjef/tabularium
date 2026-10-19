import { describe, expect, it } from "vitest";
import { fileBadge, formatDate, formatSize, shortName } from "./format";

describe("format", () => {
  it("formats plain dates and datetimes", () => {
    expect(formatDate("2025-03-14")).toBe("14 Mar 2025");
    expect(formatDate(null)).toBe("");
    expect(formatDate("not a date")).toBe("not a date");
  });

  it("formats sizes", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(2048)).toBe("2 KB");
    expect(formatSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("abbreviates hash names but keeps titles", () => {
    const hash = "a".repeat(60) + "beef";
    expect(shortName(`${hash}.pdf`)).toBe("aaaaaaaa…beef.pdf");
    expect(shortName("Electricity invoice")).toBe("Electricity invoice");
  });

  it("labels file types", () => {
    expect(fileBadge("x.pdf")).toBe("PDF");
    expect(fileBadge("noext")).toBe("FILE");
  });
});
