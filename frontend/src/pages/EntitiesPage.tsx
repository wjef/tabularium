import { useQuery } from "@tanstack/react-query";
import { Building2, FileText, Plus, Search, Trash2, Users, X } from "lucide-react";
import { useMemo, useState, type KeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type EntityDetail, type EntityOut, type EntityRef, type EntityType } from "../api/client";
import { useEntities } from "../api/hooks";
import { useAction } from "../api/mutations";
import { Dialog } from "../components/Dialog";
import { EditableText } from "../components/EditableText";
import { Card, Empty, Page } from "../components/Page";
import { formatDate } from "../lib/format";
import { matchScore, nameKey } from "../lib/text";
import ui from "../styles/ui.module.css";
import styles from "./EntitiesPage.module.css";

const ENTITY_QUERIES = [["entities"], ["entity"], ["entity-types"], ["tags"], ["documents"], ["document"]];

const useEntityTypes = () =>
  useQuery({ queryKey: ["entity-types"], queryFn: () => api.get<EntityType[]>("/entity-types") });

const NO_TYPES: EntityType[] = [];

/** People, companies and institutions that documents are tagged with. */
export function EntitiesPage() {
  const entities = useEntities().data;
  const types = useEntityTypes().data ?? NO_TYPES;
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [showTypes, setShowTypes] = useState(false);

  const duplicates = useMemo(() => {
    const seen = new Map<string, number>();
    for (const e of entities ?? []) seen.set(nameKey(e.name), (seen.get(nameKey(e.name)) ?? 0) + 1);
    return new Set([...seen].filter(([, n]) => n > 1).map(([key]) => key));
  }, [entities]);

  const groups = useMemo(() => {
    const visible = (entities ?? []).filter(
      (e) => !filter.trim() || matchScore([e.name, ...e.aliases].join(" "), filter) > 0,
    );
    return types
      .map((t) => ({ type: t, items: visible.filter((e) => e.type_id === t.id) }))
      .filter((g) => g.items.length);
  }, [entities, types, filter]);

  return (
    <Page
      title="Entities"
      subtitle="People, companies and institutions. A document tagged with an entity can be found by its name and aliases."
      wide
      actions={
        <>
          <button className={ui.secondary} onClick={() => setShowTypes(true)}>
            Entity types
          </button>
          <button className={ui.button} onClick={() => setCreating(true)}>
            <Plus size={15} aria-hidden /> New entity
          </button>
        </>
      }
    >
      <div className={styles.list}>
        <label className={styles.filter}>
          <Search size={14} aria-hidden />
          <input placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter entities" />
        </label>
        {entities && !groups.length && <Empty>{entities.length ? "Nothing matches." : "No entities yet."}</Empty>}
        {groups.map(({ type, items }) => (
          <div key={type.id}>
            <h3 className={styles.typeHeading}>{type.name}</h3>
            {items.map((e) => (
              <button
                key={e.id}
                className={e.id === selected ? `${styles.item} ${styles.selected}` : styles.item}
                onClick={() => setParams({ id: e.id })}
              >
                <span className={styles.itemName}>{e.name}</span>
                {duplicates.has(nameKey(e.name)) && (
                  <span className={styles.dupe} title="Another entity has the same name">
                    duplicate
                  </span>
                )}
                {e.tag_count > 0 && <span className={styles.count}>{e.tag_count}</span>}
              </button>
            ))}
          </div>
        ))}
      </div>
      <div className={styles.detail}>
        {selected ? (
          <EntityView key={selected} id={selected} types={types} onGone={() => setParams({})} />
        ) : (
          <Empty>Select an entity to see and change it.</Empty>
        )}
      </div>
      {creating && (
        <NewEntityDialog types={types} onClose={() => setCreating(false)} onCreated={(id) => setParams({ id })} />
      )}
      {showTypes && <TypesDialog types={types} onClose={() => setShowTypes(false)} />}
    </Page>
  );
}

function EntityView({ id, types, onGone }: { id: string; types: EntityType[]; onGone: () => void }) {
  const navigate = useNavigate();
  const entities = useEntities().data ?? [];
  const [deleting, setDeleting] = useState(false);
  const { data: e, isError, error } = useQuery({
    queryKey: ["entity", id],
    queryFn: () => api.get<EntityDetail>(`/entities/${id}`),
  });
  const update = useAction((body: Partial<Pick<EntityDetail, "name" | "type_id" | "aliases">>) =>
    api.patch<{ message: string }>(`/entities/${id}`, body), { invalidate: ENTITY_QUERIES });
  const join = useAction((group: string) => api.put<{ message: string }>(`/entities/${id}/groups/${group}`), {
    invalidate: [["entity"]],
  });
  const leave = useAction((group: string) => api.delete<{ message: string }>(`/entities/${id}/groups/${group}`), {
    invalidate: [["entity"]],
  });
  const remove = useAction(() => api.delete<{ message: string }>(`/entities/${id}`), {
    invalidate: ENTITY_QUERIES,
    success: (r) => r.message,
  });

  if (isError) return <Empty>{error.message}</Empty>;
  if (!e) return null;
  const excluded = new Set([e.id, ...e.groups.map((g) => g.id)]);
  const twins = entities.filter((x) => x.id !== e.id && nameKey(x.name) === nameKey(e.name));

  return (
    <div className={styles.detailBody}>
      <h2 className={styles.name}>
        <EditableText value={e.name} placeholder="Name" label="Entity name" onSave={(name) => update.mutate({ name })} />
      </h2>
      <div className={`${ui.row} ${styles.actionsRow}`}>
        <select
          className={ui.select}
          value={e.type_id}
          onChange={(ev) => update.mutate({ type_id: ev.target.value })}
          aria-label="Entity type"
        >
          {types.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <button
          className={ui.ghost}
          onClick={() => navigate({ pathname: "/", search: `?entity=${e.id}&related=1` })}
          disabled={!e.tag_ids.length}
          title={e.tag_ids.length ? undefined : "No document is tagged with this entity yet"}
        >
          <FileText size={14} aria-hidden /> Show documents
        </button>
      </div>

      {twins.length > 0 && (
        <div className={styles.clash} role="alert">
          Another entity has the same name:{" "}
          {twins.map((t) => (
            <button key={t.id} className={styles.refLink} onClick={() => navigate(`/entities?id=${t.id}`)}>
              {t.name} ({t.type_name}, {t.tag_count} {t.tag_count === 1 ? "tag" : "tags"})
            </button>
          ))}
          . New duplicates can no longer be created. Keep one: delete the other (possible while no tag points to
          it), or rename one to tell them apart.
        </div>
      )}

      <Card title="Aliases">
        <p className={ui.muted}>Other names that find this entity (abbreviations, former names, spellings).</p>
        <Aliases aliases={e.aliases} onChange={(aliases) => update.mutate({ aliases })} />
      </Card>

      <Card title="Member of">
        <p className={ui.muted}>
          Groups this entity belongs to, e.g. a person in a household or a company in a corporate group. Searching for
          a group with “include related” also finds its members' documents.
        </p>
        <RefList refs={e.groups} onRemove={(g) => leave.mutate(g)} />
        {e.indirect_groups.length > 0 && (
          <p className={ui.muted}>Also, through those: {e.indirect_groups.map((g) => g.name).join(", ")}</p>
        )}
        <EntityPicker
          entities={entities.filter((x) => !excluded.has(x.id))}
          placeholder="Add to a group…"
          onPick={(g) => join.mutate(g.id)}
        />
      </Card>

      {(e.members.length > 0 || e.indirect_members.length > 0) && (
        <Card title="Members">
          <RefList refs={e.members} onOpen={(m) => navigate(`/entities?id=${m}`)} />
          {e.indirect_members.length > 0 && (
            <p className={ui.muted}>Also, through those: {e.indirect_members.map((m) => m.name).join(", ")}</p>
          )}
        </Card>
      )}

      <Card title="History">
        {e.history.length ? (
          <ul className={styles.history}>
            {e.history.map((h) => (
              <li key={h.id}>
                <span className={ui.muted}>{formatDate(h.at)}</span> {h.action.toLowerCase().replace("_", " ")}
              </li>
            ))}
          </ul>
        ) : (
          <p className={ui.muted}>No changes recorded.</p>
        )}
      </Card>

      <Card title="Delete">
        <p className={ui.muted}>
          {e.tag_ids.length
            ? "Tags point to this entity; merge or delete those tags first."
            : "No tag points to this entity."}
        </p>
        <button className={ui.secondary} disabled={e.tag_ids.length > 0} onClick={() => setDeleting(true)}>
          <Trash2 size={15} aria-hidden /> Delete entity
        </button>
      </Card>

      {deleting && (
        <Dialog
          title={`Delete “${e.name}”?`}
          confirmLabel="Delete"
          danger
          busy={remove.isPending}
          onClose={() => setDeleting(false)}
          onConfirm={() => remove.mutate(undefined, { onSuccess: onGone })}
        >
          <p>No document is tagged with it. Its group memberships are removed too.</p>
        </Dialog>
      )}
    </div>
  );
}

function Aliases({ aliases, onChange }: { aliases: string[]; onChange: (aliases: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (value && !aliases.includes(value)) onChange([...aliases, value]);
    setDraft("");
  };
  return (
    <div className={ui.row}>
      {aliases.map((a) => (
        <span key={a} className={ui.pill}>
          {a}
          <button className={styles.pillX} onClick={() => onChange(aliases.filter((x) => x !== a))} aria-label={`Remove alias ${a}`}>
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        className={`${ui.input} ${styles.aliasInput}`}
        placeholder="Add an alias…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && add()}
        onBlur={add}
        aria-label="New alias"
      />
    </div>
  );
}

function RefList({ refs, onRemove, onOpen }: { refs: EntityRef[]; onRemove?: (id: string) => void; onOpen?: (id: string) => void }) {
  const navigate = useNavigate();
  if (!refs.length) return null;
  return (
    <ul className={styles.refs}>
      {refs.map((r) => (
        <li key={r.id}>
          <Users size={14} aria-hidden className={ui.muted} />
          <button className={styles.refLink} onClick={() => (onOpen ?? ((x) => navigate(`/entities?id=${x}`)))(r.id)}>
            {r.name}
          </button>
          <span className={ui.muted}>{r.type_name}</span>
          {onRemove && (
            <button className={styles.pillX} onClick={() => onRemove(r.id)} aria-label={`Leave ${r.name}`} title="Remove">
              <X size={13} />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Type to find an entity; ↑/↓ and Enter pick. */
function EntityPicker({ entities, placeholder, onPick }: { entities: EntityOut[]; placeholder: string; onPick: (e: EntityOut) => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const results = query.trim()
    ? entities
        .map((e) => ({ e, score: matchScore([e.name, ...e.aliases].join(" "), query) }))
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 8)
        .map((x) => x.e)
    : [];
  const current = Math.min(active, results.length - 1);
  const pick = (e: EntityOut) => {
    onPick(e);
    setQuery("");
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current + (event.key === "ArrowDown" ? 1 : -1) + results.length) % Math.max(results.length, 1));
    } else if (event.key === "Enter" && results[current]) {
      event.preventDefault();
      pick(results[current]);
    }
  };
  return (
    <div className={styles.picker}>
      <input
        className={ui.input}
        placeholder={placeholder}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={results.length > 0}
        aria-label={placeholder}
      />
      {results.length > 0 && (
        <ul role="listbox" className={styles.pickerList}>
          {results.map((e, i) => (
            <li
              key={e.id}
              role="option"
              aria-selected={i === current}
              className={i === current ? styles.active : undefined}
              onMouseDown={(ev) => ev.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(e)}
            >
              <Building2 size={14} aria-hidden /> {e.name} <span className={ui.muted}>{e.type_name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewEntityDialog({ types, onClose, onCreated }: { types: EntityType[]; onClose: () => void; onCreated: (id: string) => void }) {
  const entities = useEntities();
  const [name, setName] = useState("");
  const [typeId, setTypeId] = useState(types[0]?.id ?? "");
  const [created, setCreated] = useState(false); // the refreshed list then holds the new entity itself
  const clash =
    name.trim() && !created ? entities.data?.find((e) => nameKey(e.name) === nameKey(name)) : undefined;
  const create = useAction(() => api.post<{ message: string }>("/entities", { name: name.trim(), type_id: typeId }), {
    invalidate: [["entities"], ["entity-types"]],
    success: () => "Entity created.",
  });
  return (
    <Dialog
      title="New entity"
      confirmLabel="Create"
      busy={create.isPending || !name.trim() || !typeId || !!clash}
      onClose={onClose}
      onConfirm={() =>
        create.mutate(undefined, {
          onSuccess: (result) => {
            setCreated(true);
            onCreated(result.message); // the new entity's id
            onClose();
          },
        })
      }
    >
      <label className={ui.field}>
        Name
        <input className={ui.input} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </label>
      {clash && (
        <p className={styles.clash}>
          “{clash.name}” already exists ({clash.type_name}).{" "}
          <button
            type="button"
            className={styles.refLink}
            onClick={() => {
              onCreated(clash.id);
              onClose();
            }}
          >
            Open it
          </button>
          <br />
          To tell two apart, add something to the name, e.g. a city or a year.
        </p>
      )}
      <label className={ui.field}>
        Type
        <select className={ui.select} value={typeId} onChange={(e) => setTypeId(e.target.value)}>
          {types.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
    </Dialog>
  );
}

function TypesDialog({ types, onClose }: { types: EntityType[]; onClose: () => void }) {
  const [name, setName] = useState("");
  const invalidate = [["entity-types"], ["entities"], ["entity"]];
  const create = useAction((n: string) => api.post<{ message: string }>("/entity-types", { name: n }), { invalidate });
  const rename = useAction(({ id, n }: { id: string; n: string }) => api.patch<{ message: string }>(`/entity-types/${id}`, { name: n }), {
    invalidate,
  });
  const remove = useAction((id: string) => api.delete<{ message: string }>(`/entity-types/${id}`), { invalidate });
  return (
    <Dialog title="Entity types" confirmLabel="Done" onClose={onClose} onConfirm={onClose}>
      <ul className={styles.types}>
        {types.map((t) => (
          <li key={t.id}>
            <span className={styles.typeName}>
              <EditableText value={t.name} placeholder="Name" label="Type name" onSave={(n) => rename.mutate({ id: t.id, n })} />
            </span>
            <span className={ui.muted}>{t.entity_count}</span>
            <button
              type="button"
              className={styles.pillX}
              disabled={t.entity_count > 0}
              title={t.entity_count ? "In use: change those entities' type first" : "Delete type"}
              aria-label={`Delete type ${t.name}`}
              onClick={() => remove.mutate(t.id)}
            >
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
      <div className={ui.row}>
        <input
          className={ui.input}
          placeholder="New type…"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (name.trim()) create.mutate(name.trim(), { onSuccess: () => setName("") });
            }
          }}
          aria-label="New entity type"
        />
        <button
          type="button"
          className={ui.secondary}
          disabled={!name.trim()}
          onClick={() => create.mutate(name.trim(), { onSuccess: () => setName("") })}
        >
          Add
        </button>
      </div>
    </Dialog>
  );
}
