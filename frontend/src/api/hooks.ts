import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  api,
  qs,
  type AppInfo,
  type DocumentDetail,
  type DocumentPage,
  type EntityOut,
  type TagNode,
} from "./client";

export interface DocumentFilters {
  q?: string;
  tag?: string[];
  entity?: string[];
  related?: boolean;
  untagged?: boolean;
  sort?: "date" | "imported" | "imported_asc" | "title";
}

export const useInfo = () =>
  useQuery({ queryKey: ["info"], queryFn: () => api.get<AppInfo>("/info"), refetchInterval: 15_000 });

export const useTags = () => useQuery({ queryKey: ["tags"], queryFn: () => api.get<TagNode[]>("/tags") });

export const useEntities = () =>
  useQuery({ queryKey: ["entities"], queryFn: () => api.get<EntityOut[]>("/entities") });

export const useDocuments = (filters: DocumentFilters) =>
  useQuery({
    queryKey: ["documents", filters],
    queryFn: () => api.get<DocumentPage>(`/documents${qs({ ...filters, limit: 100 })}`),
    placeholderData: keepPreviousData, // keep the list while a new search loads
  });

export const useDocument = (id: string | null) =>
  useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<DocumentDetail>(`/documents/${id}`),
    enabled: !!id,
  });
