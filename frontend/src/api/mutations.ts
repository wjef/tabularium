import { useMutation, useQueryClient } from "@tanstack/react-query";
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
function useRefresh() {
  const client = useQueryClient();
  return (detail?: DocumentDetail) => {
    if (detail) client.setQueryData(["document", detail.id], detail);
    void client.invalidateQueries({ queryKey: ["documents"] });
    void client.invalidateQueries({ queryKey: ["info"] });
  };
}

const showError = (error: Error) => toast(error.message, "error");

export function useUpdateDocument(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (changes: DocumentChanges) => api.patch<DocumentDetail>(`/documents/${id}`, changes),
    onSuccess: (detail) => refresh(detail),
    onError: showError,
  });
}

export function useExtractAgain(id: string) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: () => api.post<DocumentDetail>(`/documents/${id}/extract`),
    onSuccess: (detail) => {
      refresh(detail);
      toast("The text will be read again in the background.");
    },
    onError: showError,
  });
}

export function useOutdate(id: string) {
  const client = useQueryClient();
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (reason: string) =>
      api.post<{ message: string }>(`/documents/${id}/outdate`, { reason: reason.trim() || null }),
    onSuccess: (result) => {
      client.removeQueries({ queryKey: ["document", id] });
      refresh();
      void client.invalidateQueries({ queryKey: ["tags"] });
      toast(result.message);
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

/** Tag changes also change the tag tree (new tags, counts). */
function useTagRefresh() {
  const client = useQueryClient();
  const refresh = useRefresh();
  return (detail?: DocumentDetail) => {
    refresh(detail);
    void client.invalidateQueries({ queryKey: ["tags"] });
  };
}

export function useAddTag(id: string) {
  const refresh = useTagRefresh();
  return useMutation({
    mutationFn: (choice: TagChoice) => api.post<DocumentDetail>(`/documents/${id}/tags`, assignBody(choice)),
    onSuccess: (detail) => refresh(detail),
    onError: showError,
  });
}

export function useRemoveTag(id: string) {
  const refresh = useTagRefresh();
  return useMutation({
    mutationFn: (tagId: string) => api.delete<DocumentDetail>(`/documents/${id}/tags/${tagId}`),
    onSuccess: (detail) => refresh(detail),
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
  const refresh = useTagRefresh();
  return useMutation({
    mutationFn: async ({ ids, choice }: { ids: string[]; choice: TagChoice }) => {
      const tagId = await resolveTag(choice);
      const result = await api.post<{ message: string }>("/documents/bulk-tags", { document_ids: ids, tag_id: tagId });
      return Number(result.message);
    },
    onSuccess: (added, { ids, choice }) => {
      refresh();
      ids.forEach((id) => void client.invalidateQueries({ queryKey: ["document", id] }));
      const name = choice.kind === "tag" ? choice.tag.name : choice.kind === "entity" ? choice.entity.name : choice.name;
      toast(`Tag “${name}” added to ${added} ${added === 1 ? "document" : "documents"}.`);
    },
    onError: showError,
  });
}

export function useAddLink(id: string) {
  const refresh = useRefresh();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ otherId, kind }: { otherId: string; kind: string }) =>
      api.post<DocumentDetail>(`/documents/${id}/links`, { other_id: otherId, kind }),
    onSuccess: (detail, { otherId }) => {
      refresh(detail);
      void client.invalidateQueries({ queryKey: ["document", otherId] });
    },
    onError: showError,
  });
}

export function useRemoveLink(id: string) {
  const refresh = useRefresh();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ relationId }: { relationId: string; otherId: string }) =>
      api.delete<DocumentDetail>(`/documents/${id}/links/${relationId}`),
    onSuccess: (detail, { otherId }) => {
      refresh(detail);
      void client.invalidateQueries({ queryKey: ["document", otherId] });
    },
    onError: showError,
  });
}
