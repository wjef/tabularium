import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "../components/toast";
import type { TagChoice } from "../lib/tagChoices";
import { api, type DocumentDetail } from "./client";

export interface DocumentChanges {
  title?: string;
  note?: string;
  document_date?: string;
  clear_document_date?: boolean;
}

/** After a change: the document view shows the server's answer, lists and counts refresh. */
function refreshAfter(client: QueryClient, detail?: DocumentDetail, tags = false) {
  if (detail) client.setQueryData(["document", detail.id], detail);
  void client.invalidateQueries({ queryKey: ["documents"] });
  void client.invalidateQueries({ queryKey: ["info"] });
  void client.invalidateQueries({ queryKey: ["history"] });
  if (tags) void client.invalidateQueries({ queryKey: ["tags"] });
}

const showError = (error: Error) => toast(error.message, "error");

/** A message with an "Undo" button that runs `undo` and refreshes everything it touched. */
function offerUndo(client: QueryClient, message: string, undo: () => Promise<unknown>) {
  toast(message, "info", {
    label: "Undo",
    run: () => {
      undo()
        .then((result) => {
          const detail = result && typeof result === "object" && "id" in result ? (result as DocumentDetail) : undefined;
          refreshAfter(client, detail, true);
          void client.invalidateQueries({ queryKey: ["document"] });
          void client.invalidateQueries({ queryKey: ["outdated"] });
          toast("Undone.");
        })
        .catch(showError);
    },
  });
}

const FIELD_NAMES: Record<string, string> = { title: "Title", note: "Note", document_date: "Document date" };

export function useUpdateDocument(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (changes: DocumentChanges) => api.patch<DocumentDetail>(`/documents/${id}`, changes),
    onMutate: () => client.getQueryData<DocumentDetail>(["document", id]),
    onSuccess: (detail, changes, before) => {
      refreshAfter(client, detail);
      if (!before) return;
      const field = changes.clear_document_date ? "document_date" : Object.keys(changes)[0];
      const undo: DocumentChanges =
        field === "document_date"
          ? before.document_date
            ? { document_date: before.document_date }
            : { clear_document_date: true }
          : { [field]: (before[field as "title" | "note"] as string | null) ?? "" };
      offerUndo(client, `${FIELD_NAMES[field] ?? "Document"} changed.`, () =>
        api.patch<DocumentDetail>(`/documents/${id}`, undo),
      );
    },
    onError: showError,
  });
}

export function useExtractAgain(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<DocumentDetail>(`/documents/${id}/extract`),
    onSuccess: (detail) => {
      refreshAfter(client, detail);
      toast("The text will be read again in the background.");
    },
    onError: showError,
  });
}

export function useOutdate(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) =>
      api.post<{ message: string }>(`/documents/${id}/outdate`, { reason: reason.trim() || null }),
    onSuccess: (result) => {
      client.removeQueries({ queryKey: ["document", id] });
      refreshAfter(client, undefined, true);
      void client.invalidateQueries({ queryKey: ["outdated"] });
      offerUndo(client, result.message, () => api.post(`/outdated/${id}/restore`));
    },
    onError: showError,
  });
}

/** Body for POST /documents/{id}/tags from a picked suggestion. */
function assignBody(choice: TagChoice) {
  if (choice.kind === "tag") return { tag_id: choice.tag.id };
  if (choice.kind === "entity") return { entity_id: choice.entity.id, parent_id: choice.parent.id };
  return { name: choice.name, parent_id: choice.parent?.id ?? null };
}

export function useAddTag(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (choice: TagChoice) => api.post<DocumentDetail>(`/documents/${id}/tags`, assignBody(choice)),
    onSuccess: (detail) => refreshAfter(client, detail, true),
    onError: showError,
  });
}

export function useRemoveTag(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (tagId: string) => api.delete<DocumentDetail>(`/documents/${id}/tags/${tagId}`),
    onMutate: (tagId) => client.getQueryData<DocumentDetail>(["document", id])?.tags.find((t) => t.id === tagId),
    onSuccess: (detail, tagId, tag) => {
      refreshAfter(client, detail, true);
      offerUndo(client, `Tag “${tag?.name ?? "tag"}” removed.`, () =>
        api.post<DocumentDetail>(`/documents/${id}/tags`, { tag_id: tagId }),
      );
    },
    onError: showError,
  });
}

/** The tag's id, creating the tag first when the choice is a new one. */
async function resolveTag(choice: TagChoice): Promise<string> {
  if (choice.kind === "tag") return choice.tag.id;
  const body =
    choice.kind === "entity"
      ? { entity_id: choice.entity.id, parent_id: choice.parent.id }
      : { name: choice.name, parent_id: choice.parent?.id ?? null };
  return (await api.post<{ message: string }>("/tags", body)).message; // the new tag's id
}

export function useBulkTag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ ids, choice }: { ids: string[]; choice: TagChoice }) => {
      const tagId = await resolveTag(choice);
      const result = await api.post<{ message: string }>("/documents/bulk-tags", { document_ids: ids, tag_id: tagId });
      return Number(result.message);
    },
    onSuccess: (added, { ids, choice }) => {
      refreshAfter(client, undefined, true);
      ids.forEach((id) => void client.invalidateQueries({ queryKey: ["document", id] }));
      const name = choice.kind === "tag" ? choice.tag.name : choice.kind === "entity" ? choice.entity.name : choice.name;
      toast(`Tag “${name}” added to ${added} ${added === 1 ? "document" : "documents"}.`);
    },
    onError: showError,
  });
}

export function useAddLink(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ otherId, kind }: { otherId: string; kind: string }) =>
      api.post<DocumentDetail>(`/documents/${id}/links`, { other_id: otherId, kind }),
    onSuccess: (detail, { otherId }) => {
      refreshAfter(client, detail);
      void client.invalidateQueries({ queryKey: ["document", otherId] });
    },
    onError: showError,
  });
}

export function useRemoveLink(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ relationId }: { relationId: string; otherId: string }) =>
      api.delete<DocumentDetail>(`/documents/${id}/links/${relationId}`),
    onMutate: ({ relationId }) =>
      client.getQueryData<DocumentDetail>(["document", id])?.links.find((l) => l.relation_id === relationId),
    onSuccess: (detail, { otherId }, link) => {
      refreshAfter(client, detail);
      void client.invalidateQueries({ queryKey: ["document", otherId] });
      if (link) {
        offerUndo(client, "Link removed.", () =>
          api.post<DocumentDetail>(`/documents/${id}/links`, {
            other_id: link.other.id,
            kind: `${link.kind}:${link.direction}`,
          }),
        );
      }
    },
    onError: showError,
  });
}

interface ActionOptions<A, R> {
  /** query keys to refresh afterwards */
  invalidate?: unknown[][];
  /** message shown on success (nothing when it returns undefined) */
  success?: (result: R, args: A) => string | undefined;
}

/** Any change made through the API: shows errors, refreshes the given queries. */
export function useAction<A = void, R = { message: string }>(
  fn: (args: A) => Promise<R>,
  { invalidate = [], success }: ActionOptions<A, R> = {},
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result, args) => {
      for (const key of [...invalidate, ["history"]]) void client.invalidateQueries({ queryKey: key });
      const message = success?.(result, args);
      if (message) toast(message);
    },
    onError: showError,
  });
}

/** Finish the initial tagging (the document leaves "To be tagged") or send it back. */
export function useTaggingDone(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (done: boolean) => api.post<DocumentDetail>(`/documents/${id}/tagging`, { done }),
    onSuccess: (detail, done) => {
      refreshAfter(client, detail);
      offerUndo(client, done ? "Tagging done." : "Back in To be tagged.", () =>
        api.post<DocumentDetail>(`/documents/${id}/tagging`, { done: !done }),
      );
    },
    onError: showError,
  });
}

export function useBulkTaggingDone() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, done }: { ids: string[]; done: boolean }) =>
      api.post<{ message: string }>("/documents/bulk-tagging", { document_ids: ids, done }),
    onSuccess: (result, { ids, done }) => {
      refreshAfter(client);
      ids.forEach((id) => void client.invalidateQueries({ queryKey: ["document", id] }));
      const n = Number(result.message);
      const what = `${n} ${n === 1 ? "document" : "documents"}`;
      toast(done ? `Tagging done for ${what}.` : `${what} back in To be tagged.`);
    },
    onError: showError,
  });
}
