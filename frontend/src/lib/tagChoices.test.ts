import { describe, expect, it } from "vitest";
import type { EntityOut, TagNode } from "../api/client";
import { choiceLabel, suggest } from "./tagChoices";

const tag = (id: string, path: string, parent: string | null, count = 1, entity: string | null = null): TagNode => ({
  id,
  path,
  name: path.split(" / ").at(-1)!,
  parent_id: parent,
  count,
  total: count,
  entity_id: entity,
  color: null,
  sort_order: 0,
});

const tags = [
  tag("recipient", "recipient", null, 0), // holds no entities yet
  tag("home", "home", null, 0),
  tag("elec", "home / electricity", "home", 5),
  tag("inv", "invoice", null, 9),
  tag("sender", "sender", null, 0),
  tag("anna", "sender / Anna Nowak", "sender", 2, "e-anna"),
];
const entity = (id: string, name: string, aliases: string[] = []): EntityOut => ({
  id,
  name,
  aliases,
  attributes: {},
  tag_count: 0,
  type_id: "t",
  type_name: "Person",
});
const entities = [entity("e-anna", "Anna Nowak"), entity("e-jan", "Jan Kowalski", ["JK"])];
const labels = (q: string, assigned = new Set<string>()) => suggest(q, tags, entities, assigned).map(choiceLabel);

describe("tag suggestions", () => {
  it("shows the most used tags when nothing is typed", () => {
    expect(labels("")).toEqual(["invoice", "home / electricity", "sender / Anna Nowak"]);
  });

  it("matches tag names first, then offers to create", () => {
    expect(labels("elec")).toEqual(["home / electricity", "elec"]);
    expect(labels("invoice")).toEqual(["invoice"]); // exists: no "create"
  });

  it("leaves out assigned tags", () => {
    expect(labels("elec", new Set(["elec"]))).toEqual(["elec"]);
  });

  it("offers entities under tags that hold entities", () => {
    expect(labels("kowalski")).toEqual(["sender / Jan Kowalski", "kowalski"]);
    expect(labels("jk")).toContain("sender / Jan Kowalski");
    expect(labels("anna")).not.toContain("sender / Anna Nowak (new)");
  });

  it("creates under an existing parent with a path", () => {
    const [choice] = suggest("home / garden", tags, entities, new Set()).filter((c) => c.kind === "new");
    expect(choice).toMatchObject({ kind: "new", name: "garden", parent: { id: "home" } });
    expect(suggest("nowhere / garden", tags, entities, new Set()).some((c) => c.kind === "new")).toBe(false);
  });

  it("offers entities under a typed parent, even one without entities yet", () => {
    const choices = suggest("recipient / kowal", tags, entities, new Set());
    expect(choices[0]).toMatchObject({ kind: "entity", entity: { id: "e-jan" }, parent: { id: "recipient" } });
    expect(choices.map(choiceLabel)).toContain("recipient / kowal"); // a plain tag stays possible, last
    expect(choices.at(-1)).toMatchObject({ kind: "new" });
    // an entity already filed there is offered as the existing tag, not again as new
    expect(suggest("sender / anna", tags, entities, new Set()).map(choiceLabel)[0]).toBe("sender / Anna Nowak");
  });
});
