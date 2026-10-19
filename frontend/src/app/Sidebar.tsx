import {
  Archive,
  Building2,
  ChevronRight,
  FileText,
  History,
  Inbox,
  Monitor,
  Moon,
  Settings2,
  Sun,
  Tag as TagIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { TagNode } from "../api/client";
import { useInfo, useTags } from "../api/hooks";
import { useTheme, type ThemeChoice } from "../lib/theme";
import styles from "./Sidebar.module.css";
import { useView } from "./useView";

const THEMES: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "System theme", Icon: Monitor },
  { value: "light", label: "Light theme", Icon: Sun },
  { value: "dark", label: "Dark theme", Icon: Moon },
];

/** Old (server-rendered) screens, used until the SPA has its own. */
const LEGACY = { entities: "/entities", history: "/history", maintenance: "/maintenance", outdated: "/outdated" };

export function Sidebar() {
  const { section, filters, update } = useView();
  const info = useInfo().data;
  const tags = useTags().data ?? [];
  const [theme, setTheme] = useTheme();
  const activeTag = filters.tag?.length === 1 ? filters.tag[0] : null;
  const browsingAll = section === "all" && !filters.tag?.length && !filters.entity?.length;

  return (
    <nav className={styles.sidebar} aria-label="Main">
      <div className={styles.brand}>
        <span className={styles.logo} aria-hidden>
          <Archive size={16} strokeWidth={2.4} />
        </span>
        Tabularium
      </div>

      <div className={styles.nav}>
        <NavItem
          icon={FileText}
          label="All documents"
          active={browsingAll}
          onClick={() => update({ section: null, tag: null, entity: null, doc: null })}
        />
        <NavItem
          icon={Inbox}
          label="To be tagged"
          count={info?.untagged}
          highlight={!!info?.untagged}
          active={section === "inbox"}
          onClick={() => update({ section: "inbox", tag: null, entity: null, doc: null, sort: null })}
        />
        <NavItem icon={Archive} label="Outdated" count={info?.outdated} href={LEGACY.outdated} />
      </div>

      <div className={styles.heading}>Tags</div>
      <div className={styles.tree}>
        <TagTree
          tags={tags}
          activeId={activeTag}
          onSelect={(id) => update({ section: null, tag: id, entity: null, doc: null })}
        />
      </div>

      <div className={styles.footer}>
        <NavItem icon={Building2} label="Entities" href={LEGACY.entities} />
        <NavItem icon={History} label="History" href={LEGACY.history} />
        <NavItem icon={Settings2} label="Maintenance" href={LEGACY.maintenance} />
        <div className={styles.themes} role="radiogroup" aria-label="Theme">
          {THEMES.map(({ value, label, Icon }) => (
            <button
              key={value}
              role="radio"
              aria-checked={theme === value}
              aria-label={label}
              title={label}
              className={theme === value ? styles.themeActive : undefined}
              onClick={() => setTheme(value)}
            >
              <Icon size={14} />
            </button>
          ))}
          {info && <span className={styles.version}>v{info.version}</span>}
        </div>
      </div>
    </nav>
  );
}

interface NavItemProps {
  icon: typeof Sun;
  label: string;
  count?: number;
  highlight?: boolean;
  active?: boolean;
  href?: string;
  onClick?: () => void;
}

function NavItem({ icon: Icon, label, count, highlight, active, href, onClick }: NavItemProps) {
  const content = (
    <>
      <Icon size={16} aria-hidden />
      <span className={styles.label}>{label}</span>
      {count !== undefined && count > 0 && (
        <span className={highlight ? styles.badgeStrong : styles.badge}>{count}</span>
      )}
    </>
  );
  const className = active ? `${styles.item} ${styles.active}` : styles.item;
  return href ? (
    <a className={className} href={href}>
      {content}
    </a>
  ) : (
    <button className={className} onClick={onClick} aria-current={active ? "page" : undefined}>
      {content}
    </button>
  );
}

interface TreeProps {
  tags: TagNode[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

/** Nested tag list; branches open on click, the path to the selected tag stays open. */
function TagTree({ tags, activeId, onSelect }: TreeProps) {
  const children = useMemo(() => {
    const map = new Map<string | null, TagNode[]>();
    for (const tag of tags) {
      const list = map.get(tag.parent_id) ?? [];
      list.push(tag);
      map.set(tag.parent_id, list);
    }
    for (const list of map.values()) list.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
    return map;
  }, [tags]);

  const ancestors = useMemo(() => {
    const byId = new Map(tags.map((t) => [t.id, t]));
    const open = new Set<string>();
    let current = activeId ? byId.get(activeId) : undefined;
    while (current?.parent_id) {
      open.add(current.parent_id);
      current = byId.get(current.parent_id);
    }
    return open;
  }, [tags, activeId]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const render = (parent: string | null, depth: number) =>
    (children.get(parent) ?? []).map((tag) => {
      const hasChildren = children.has(tag.id);
      const expanded = open.has(tag.id) || ancestors.has(tag.id);
      return (
        <li key={tag.id}>
          <div
            className={tag.id === activeId ? `${styles.tag} ${styles.active}` : styles.tag}
            style={{ paddingLeft: 8 + depth * 14 }}
          >
            {hasChildren ? (
              <button
                className={expanded ? `${styles.twisty} ${styles.open}` : styles.twisty}
                onClick={() => toggle(tag.id)}
                aria-label={expanded ? `Collapse ${tag.name}` : `Expand ${tag.name}`}
                aria-expanded={expanded}
              >
                <ChevronRight size={13} />
              </button>
            ) : (
              <span className={styles.twistySpace} />
            )}
            <button className={styles.tagName} onClick={() => onSelect(tag.id)} title={tag.path}>
              {tag.entity_id ? <Building2 size={13} aria-hidden /> : <TagIcon size={13} aria-hidden />}
              <span className={styles.label}>{tag.name}</span>
              {tag.count > 0 && <span className={styles.count}>{tag.count}</span>}
            </button>
          </div>
          {hasChildren && expanded && <ul>{render(tag.id, depth + 1)}</ul>}
        </li>
      );
    });

  if (!tags.length) return <p className={styles.empty}>No tags yet.</p>;
  return <ul>{render(null, 0)}</ul>;
}
