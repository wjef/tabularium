import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "../components/toast";
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
