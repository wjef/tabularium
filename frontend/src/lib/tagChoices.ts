import type { EntityOut, TagNode } from "../api/client";
import { fold, matchScore } from "./text";

/** What the user can pick in the tag box. */
export type TagChoice =
  | { kind: "tag"; tag: TagNode }
  | { kind: "entity"; entity: EntityOut; parent: TagNode } // new entity tag under parent
  | { kind: "new"; name: string; parent: TagNode | null }; // brand-new tag

export const choiceKey = (c: TagChoice) =>
  c.kind === "tag" ? c.tag.id : c.kind === "entity" ? `e:${c.parent.id}:${c.entity.id}` : `n:${c.name}`;

export const choiceLabel = (c: TagChoice) =>
  c.kind === "tag" ? c.tag.path : c.kind === "entity" ? `${c.parent.path} / ${c.entity.name}` : c.parent ? `${c.parent.path} / ${c.name}` : c.name;

const LIMIT = 8;

/**
 * Suggestions for the typed text: matching tags, then entities that could be filed under a
 * tag that already holds entities (e.g. "sender"), then "create a new tag". `assigned` tags are
 * left out. Typing "home / garden" creates "garden" under the existing tag "home".
 */
export function suggest(query: string, tags: TagNode[], entities: EntityOut[], assigned: Set<string>): TagChoice[] {
  const q = query.trim();
  if (!q) {
    // nothing typed: the most used tags
    return tags
      .filter((t) => !assigned.has(t.id) && t.count > 0)
      .sort((a, b) => b.count - a.count)
      .slice(0, LIMIT)
      .map((tag) => ({ kind: "tag", tag }));
  }

  const scored = tags
    .filter((t) => !assigned.has(t.id))
    .map((tag) => {
      const own = matchScore(tag.name, q);
      return { tag, score: own ? own * 3 : matchScore(tag.path, q) };
    })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || b.tag.count - a.tag.count || a.tag.path.localeCompare(b.tag.path));
  const choices: TagChoice[] = scored.slice(0, LIMIT).map(({ tag }) => ({ kind: "tag", tag }));

  // entities without a tag yet under a parent that is used for entities
  const byId = new Map(tags.map((t) => [t.id, t]));
  const entityParents = [...new Set(tags.filter((t) => t.entity_id && t.parent_id).map((t) => t.parent_id!))]
    .map((id) => byId.get(id))
    .filter((t): t is TagNode => !!t);
  const existing = new Set(tags.filter((t) => t.entity_id).map((t) => `${t.parent_id}:${t.entity_id}`));
  for (const entity of entities) {
    if (choices.length >= LIMIT + 3) break;
    const names = [entity.name, ...entity.aliases];
    if (!names.some((n) => matchScore(n, q))) continue;
    for (const parent of entityParents) {
      if (!existing.has(`${parent.id}:${entity.id}`)) choices.push({ kind: "entity", entity, parent });
    }
  }

  const parts = q.split("/").map((p) => p.trim()).filter(Boolean);
  const name = parts.at(-1);
  const parentPath = fold(parts.slice(0, -1).join(" / "));
  const parent = parts.length > 1 ? tags.find((t) => fold(t.path) === parentPath) ?? null : null;
  const fullPath = fold(parts.join(" / "));
  const exists = tags.some((t) => fold(t.path) === fullPath);
  if (name && !exists && (parts.length === 1 || parent)) choices.push({ kind: "new", name, parent });
  return choices;
}
