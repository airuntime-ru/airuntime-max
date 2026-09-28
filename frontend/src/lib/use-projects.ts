"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { listProjects, type ProjectType } from "@/lib/api";

const PAGE_SIZE = 20;

export function useProjects() {
  const [projects, setProjects] = useState<ProjectType[]>([]);
  const [total, setTotal] = useState(0);
  const [deployedTotal, setDeployedTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listProjects(PAGE_SIZE, 0);
      setProjects(result.items);
      setTotal(result.total);
      setDeployedTotal(result.deployed_total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const result = await listProjects(PAGE_SIZE, projects.length);
      setProjects((prev) => [...prev, ...result.items]);
      setTotal(result.total);
      setDeployedTotal(result.deployed_total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    } finally {
      setLoadingMore(false);
    }
  }, [projects.length]);

  const hasMore = projects.length < total;

  return useMemo(
    () => ({
      projects,
      total,
      deployedTotal,
      error,
      loading,
      loadingMore,
      hasMore,
      refresh,
      loadMore,
    }),
    [projects, total, deployedTotal, error, loading, loadingMore, hasMore, refresh, loadMore]
  );
}
