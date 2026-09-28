"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { CreateProjectModal } from "@/components/app/create-project-modal";

type CreateProjectContextValue = {
  openCreateProject: () => void;
};

const CreateProjectContext = createContext<CreateProjectContextValue>({
  openCreateProject: () => {},
});

/**
 * Holds the single "new project" dialog for the whole cabinet, so the sidebar, the mobile
 * menu and the projects screen all trigger the same one instead of each owning a copy.
 */
export function CreateProjectProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ openCreateProject: () => setOpen(true) }), []);

  return (
    <CreateProjectContext.Provider value={value}>
      {children}
      {/* No list refresh needed: a successful create navigates straight to the project chat,
          and /app refetches on its next mount. */}
      <CreateProjectModal open={open} onClose={() => setOpen(false)} />
    </CreateProjectContext.Provider>
  );
}

export function useCreateProject(): CreateProjectContextValue {
  return useContext(CreateProjectContext);
}
