const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** "14 Mar 2025" from an ISO date or datetime. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = value.length === 10 ? new Date(`${value}T00:00:00`) : new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateFormat.format(date);
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Short label for a file type badge: PDF, JPG, TXT… */
export function fileBadge(storedName: string): string {
  const ext = storedName.includes(".") ? storedName.split(".").pop() ?? "" : "";
  return ext ? ext.toUpperCase().slice(0, 4) : "FILE";
}

/** Long hashes are unreadable: show the first and last characters. */
export function shortName(name: string): string {
  const match = /^([0-9a-f]{64})(\.\w+)?$/.exec(name);
  return match ? `${match[1].slice(0, 8)}…${match[1].slice(-4)}${match[2] ?? ""}` : name;
}
