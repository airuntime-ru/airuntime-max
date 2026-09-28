"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Download, FileText, Folder, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, PageLoader } from "@/components/ui/loader";
import { Modal } from "@/components/ui/modal";
import {
  downloadProjectVersionArchive,
  getProjectVersionFile,
  listProjectVersionTree,
  listProjectVersions,
  rollbackProjectVersion,
  type ProjectVersionFileType,
  type ProjectVersionTreeEntryType,
  type ProjectVersionType,
} from "@/lib/api";

const COMMITS_PAGE_SIZE = 5;
/** Soft cap for DOM preview — backend may already truncate; keep renderer light. */
const PREVIEW_CHAR_CAP = 200_000;

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function shortHash(hash: string) {
  return hash?.slice(0, 8) ?? "";
}

export default function ProjectVersionsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;

  const [versions, setVersions] = useState<ProjectVersionType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [commitsPage, setCommitsPage] = useState(0);

  const [downloading, setDownloading] = useState<string | null>(null);
  const [rollingBack, setRollingBack] = useState<string | null>(null);
  const [confirmRollback, setConfirmRollback] = useState<ProjectVersionType | null>(null);

  const [activeCommitHash, setActiveCommitHash] = useState<string | null>(null);
  const [treePath, setTreePath] = useState("");
  const [treeEntries, setTreeEntries] = useState<ProjectVersionTreeEntryType[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);

  const [filePath, setFilePath] = useState<string | null>(null);
  const [fileData, setFileData] = useState<ProjectVersionFileType | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const fileRequestId = useRef(0);

  const joinPath = useCallback((base: string, name: string) => {
    if (!base) return name;
    return `${base}/${name}`;
  }, []);

  const loadVersions = useCallback(async (silent?: boolean) => {
    if (!projectId) return;
    if (!silent) setLoading(true);
    try {
      const rows = await listProjectVersions(projectId);
      setVersions(rows);
      setCommitsPage(0);
      setActiveCommitHash((prev) => {
        if (!rows.length) return null;
        if (prev && rows.some((row) => row.commit_hash === prev)) return prev;
        return rows[0].commit_hash;
      });
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить версии");
      setVersions([]);
      setActiveCommitHash(null);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void loadVersions();
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadVersions]);

  const hasVersions = useMemo(() => versions.length > 0, [versions]);
  const commitsPageCount = Math.max(1, Math.ceil(versions.length / COMMITS_PAGE_SIZE));
  const pagedVersions = useMemo(() => {
    const start = commitsPage * COMMITS_PAGE_SIZE;
    return versions.slice(start, start + COMMITS_PAGE_SIZE);
  }, [versions, commitsPage]);

  const onDownload = async (commitHash: string) => {
    if (!projectId) return;
    setDownloading(commitHash);
    try {
      const blob = await downloadProjectVersionArchive(projectId, commitHash);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `project-${projectId.slice(0, 8)}-${shortHash(commitHash)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось скачать архив");
    } finally {
      setDownloading(null);
    }
  };

  const onRollback = async (commitHash: string) => {
    if (!projectId) return;
    setConfirmRollback(null);
    setRollingBack(commitHash);
    try {
      await rollbackProjectVersion(projectId, commitHash);
      setError("");
      await loadVersions(true);
      // Кинем пользователя в историю деплоев, чтобы он увидел прогресс.
      router.push(`/app/projects/${projectId}/deployments`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить откат");
    } finally {
      setRollingBack(null);
    }
  };

  const loadTree = useCallback(
    async (silent?: boolean) => {
      if (!projectId || !activeCommitHash) return;
      if (!silent) {
        setTreeLoading(true);
      }
      try {
        const entries = await listProjectVersionTree(projectId, activeCommitHash, treePath);
        setTreeEntries(entries);
        setError("");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить дерево файлов");
        setTreeEntries([]);
      } finally {
        setTreeLoading(false);
      }
    },
    [projectId, activeCommitHash, treePath]
  );

  useEffect(() => {
    if (!activeCommitHash) return;
    const id = window.setTimeout(() => {
      void loadTree(true);
    }, 0);
    return () => window.clearTimeout(id);
  }, [activeCommitHash, treePath, loadTree]);

  const clearFilePreview = useCallback(() => {
    fileRequestId.current += 1;
    setFilePath(null);
    setFileData(null);
    setFileLoading(false);
  }, []);

  const loadFile = useCallback(
    async (commitHash: string, path: string) => {
      if (!projectId) return;
      const requestId = ++fileRequestId.current;
      setFileLoading(true);
      setFilePath(path);
      setFileData(null);
      try {
        const data = await getProjectVersionFile(projectId, commitHash, path);
        if (requestId !== fileRequestId.current) return;
        setFileData(data);
        setError("");
      } catch (e) {
        if (requestId !== fileRequestId.current) return;
        setError(e instanceof Error ? e.message : "Не удалось загрузить файл");
        setFileData(null);
        setFilePath(null);
      } finally {
        if (requestId === fileRequestId.current) setFileLoading(false);
      }
    },
    [projectId]
  );

  const segments = useMemo(() => treePath.split("/").filter(Boolean), [treePath]);
  const breadcrumbs = useMemo(() => {
    const items: { path: string; label: string }[] = [];
    items.push({ path: "", label: "/" });
    let cur = "";
    for (const seg of segments) {
      cur = cur ? `${cur}/${seg}` : seg;
      items.push({ path: cur, label: seg });
    }
    return items;
  }, [segments]);

  if (loading) return <PageLoader />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm leading-7 text-[var(--ar-mist)]">Файлы проекта и сохраненные снимки результата.</p>
        {error ? <Badge className="bg-rose-50 text-rose-700 border-rose-200">{error}</Badge> : null}
      </div>

      {!hasVersions ? (
        <EmptyState
          title="Файлов пока нет"
          description="После первой генерации здесь появятся файлы проекта. Сгенерируйте проект в чате."
          action={
            <Button variant="accent" onClick={() => router.push(`/app/projects/${projectId}/chat`)}>
              Перейти в чат
            </Button>
          }
        />
      ) : null}

      {activeCommitHash ? (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <Card hover={false} className="p-4">
            <div className="flex flex-col gap-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold">Файлы commit {shortHash(activeCommitHash)}</p>
                  <p className="mt-1 text-xs text-[var(--ar-stone)]">Просмотр содержимого (только текст).</p>
                </div>
                <Badge className="border-black/10 bg-black/5 text-[var(--ar-stone)]">{treeLoading ? "..." : "готово"}</Badge>
              </div>

              <div className="flex flex-wrap items-center gap-2 text-xs">
                {breadcrumbs.map((b, idx) => (
                  <button
                    key={`${b.path}-${idx}`}
                    type="button"
                    className={b.path === treePath ? "font-bold text-[var(--ar-black)]" : "text-[var(--ar-stone)] hover:text-[var(--ar-black)]"}
                    onClick={() => {
                      setTreePath(b.path);
                      clearFilePreview();
                    }}
                  >
                    {b.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 space-y-2">
              {treeEntries.length === 0 ? (
                <EmptyState className="border-0 bg-transparent shadow-none" title="Папка пуста" description="В этом месте репозитория пока нет файлов." />
              ) : null}

              {treeEntries.map((e) => {
                const nextPath = joinPath(treePath, e.name);
                if (e.entry_type === "tree") {
                  return (
                    <Button
                      key={e.name}
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-full justify-start"
                      onClick={() => {
                        setTreePath(nextPath);
                        clearFilePreview();
                      }}
                    >
                      <Folder size={15} />
                      {e.name}
                    </Button>
                  );
                }

                return (
                  <Button
                    key={e.name}
                    type="button"
                    variant="outline"
                    size="sm"
                    className="w-full justify-start"
                    disabled={fileLoading}
                    onClick={() => void (activeCommitHash ? loadFile(activeCommitHash, nextPath) : undefined)}
                  >
                    <FileText size={15} />
                    {e.name}
                  </Button>
                );
              })}
            </div>
          </Card>

          <Card hover={false} className="p-4">
            {!fileData ? (
              <EmptyState className="border-0 bg-transparent shadow-none" title="Выберите файл" description="Нажмите на файл в дереве слева, чтобы увидеть содержимое." />
            ) : fileData.is_binary ? (
              <EmptyState
                className="border-0 bg-transparent shadow-none"
                title="Файл бинарный"
                description="Этот файл не подходит для текстового просмотра. Можно скачать весь ZIP снапшота."
                action={
                  <Button
                    variant="accent"
                    onClick={() => void onDownload(activeCommitHash)}
                  >
                    <Download size={15} />
                    ZIP
                  </Button>
                }
              />
            ) : (
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold break-all">{filePath}</p>
                    <p className="mt-1 text-xs text-[var(--ar-stone)]">
                      {fileData.truncated ? "усечённый файл" : "полностью"}, размер: {fileData.size_bytes} байт
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void onDownload(activeCommitHash)}
                    disabled={downloading === activeCommitHash}
                  >
                    <Download size={15} />
                    ZIP
                  </Button>
                </div>

                {fileLoading ? (
                  <PageLoader />
                ) : (
                  <pre
                    className="max-h-[70vh] overflow-auto whitespace-pre-wrap bg-[#f7f8fa] p-4 font-mono text-xs leading-relaxed text-[var(--ar-graphite)] [content-visibility:auto]"
                    style={{ containIntrinsicSize: "0 480px" }}
                  >
                    {fileData.content.length > PREVIEW_CHAR_CAP
                      ? `${fileData.content.slice(0, PREVIEW_CHAR_CAP)}\n\n… превью обрезано (${fileData.content.length.toLocaleString("ru-RU")} символов)`
                      : fileData.content}
                  </pre>
                )}
              </div>
            )}
          </Card>
        </div>
      ) : null}

      {hasVersions ? (
        <div className="space-y-3">
          <p className="text-sm font-semibold text-[var(--ar-black)]">История коммитов</p>
          {pagedVersions.map((v) => (
            <Card key={v.commit_hash} className="space-y-3 p-4" hover={false}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className="border-black/10 bg-black/5 text-[var(--ar-stone)]">commit {shortHash(v.commit_hash)}</Badge>
                    {v.commit_hash === activeCommitHash ? (
                      <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">открыт</Badge>
                    ) : null}
                    <span className="text-xs text-[var(--ar-stone)]">{formatDateTime(v.created_at)}</span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-[var(--ar-black)] break-words">{v.message || "—"}</p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void onDownload(v.commit_hash)}
                    disabled={downloading === v.commit_hash}
                  >
                    <Download size={15} />
                    {downloading === v.commit_hash ? "..." : "ZIP"}
                  </Button>
                  {/* Rollback replaces the running version - it should not be the loudest
                      button on every row. Browsing the files is the safe default. */}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmRollback(v)}
                    disabled={rollingBack === v.commit_hash}
                  >
                    <RotateCcw size={15} />
                    {rollingBack === v.commit_hash ? "..." : "Откатиться"}
                  </Button>
                  <Button
                    variant="accent"
                    size="sm"
                    onClick={() => {
                      setActiveCommitHash(v.commit_hash);
                      setTreePath("");
                      clearFilePreview();
                      setError("");
                    }}
                  >
                    <FileText size={15} />
                    Открыть файлы
                  </Button>
                </div>
              </div>
            </Card>
          ))}

          {versions.length > COMMITS_PAGE_SIZE ? (
            <div className="flex items-center justify-center gap-3 pt-1">
              <Button
                variant="outline"
                size="sm"
                disabled={commitsPage <= 0}
                onClick={() => setCommitsPage((page) => Math.max(0, page - 1))}
              >
                <ChevronLeft size={15} />
                Назад
              </Button>
              <span className="text-xs tabular-nums text-[var(--ar-stone)]">
                {commitsPage + 1} / {commitsPageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={commitsPage >= commitsPageCount - 1}
                onClick={() => setCommitsPage((page) => Math.min(commitsPageCount - 1, page + 1))}
              >
                Вперёд
                <ChevronRight size={15} />
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}

      <Modal
        open={Boolean(confirmRollback)}
        onClose={() => setConfirmRollback(null)}
        title="Откатиться на эту версию?"
        description={
          confirmRollback
            ? `Проект вернётся к состоянию commit ${shortHash(confirmRollback.commit_hash)} от ${formatDateTime(confirmRollback.created_at)}. Более поздние изменения останутся в истории версий, но текущий код проекта заменится.`
            : undefined
        }
      >
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={() => setConfirmRollback(null)}>
            Отмена
          </Button>
          <Button
            variant="accent"
            onClick={() => confirmRollback && void onRollback(confirmRollback.commit_hash)}
          >
            <RotateCcw size={15} />
            Да, откатиться
          </Button>
        </div>
      </Modal>
    </div>
  );
}
