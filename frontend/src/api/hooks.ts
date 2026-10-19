import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
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

const PAGE = 50;

/** Documents matching the filters, loaded page by page as the list scrolls. */
export const useDocuments = (filters: DocumentFilters) =>
  useInfiniteQuery({
    queryKey: ["documents", filters],
    queryFn: ({ pageParam }) =>
      api.get<DocumentPage>(`/documents${qs({ ...filters, offset: pageParam, limit: PAGE })}`),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.items.length;
      return next < last.total ? next : undefined;
    },
    placeholderData: keepPreviousData, // keep the list while a new search loads
  });

export const useDocument = (id: string | null) =>
  useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<DocumentDetail>(`/documents/${id}`),
    enabled: !!id,
  });
