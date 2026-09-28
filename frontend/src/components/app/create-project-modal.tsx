"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { createProject } from "@/lib/api";

type CreateProjectModalProps = {
  open: boolean;
  onClose: () => void;
  onCreated?: () => void | Promise<void>;
};

export function CreateProjectModal({ open, onClose, onCreated }: CreateProjectModalProps) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);

  const reset = () => {
    setName("");
    setCreateError("");
    setCreating(false);
  };

  const handleClose = () => {
    if (creating) return;
    reset();
    onClose();
  };

  const onCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setCreateError("");
    try {
      const project = await createProject({ name: name.trim() });
      await onCreated?.();
      reset();
      onClose();
      router.push(`/app/projects/${project.id}/chat`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Не удалось создать проект");
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="Новый проект"
      description="Создайте пустой проект и сразу опишите задачу в чате."
    >
      <div data-tour="project-create-form" className="space-y-5">
        <div className="space-y-2">
          <label htmlFor="project-name" className="text-sm font-semibold text-[var(--ar-black)]">
            Название
          </label>
          <Input
            id="project-name"
            placeholder="Например: Запуск для консультаций"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={creating}
          />
        </div>

        {createError ? <p className="text-sm text-rose-600">{createError}</p> : null}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={handleClose} disabled={creating}>
            Отмена
          </Button>
          <Button variant="accent" onClick={() => void onCreate()} disabled={creating || !name.trim()}>
            <Sparkles size={16} />
            {creating ? "Открываем портал..." : "Создать и перейти в чат"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
