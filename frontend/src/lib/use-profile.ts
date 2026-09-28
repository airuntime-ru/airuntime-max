"use client";

import { useCallback, useEffect, useState } from "react";

import { getMe, type MeType } from "@/lib/api";

export function useProfile() {
  const [profile, setProfile] = useState<MeType | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setProfile(await getMe());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить профиль");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      try {
        const row = await getMe();
        if (!active) return;
        setProfile(row);
        setError("");
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить профиль");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  return { profile, error, loading, refresh };
}
