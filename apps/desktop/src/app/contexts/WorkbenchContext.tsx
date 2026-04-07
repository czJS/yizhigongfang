import React, { createContext, useContext } from "react";

export type WorkbenchContextValue = any;

const WorkbenchContext = createContext<WorkbenchContextValue | null>(null);

export function WorkbenchProvider(props: { value: WorkbenchContextValue; children: React.ReactNode }) {
  return <WorkbenchContext.Provider value={props.value}>{props.children}</WorkbenchContext.Provider>;
}

export function useWorkbenchCtx(): WorkbenchContextValue {
  const v = useContext(WorkbenchContext);
  if (!v) throw new Error("useWorkbenchCtx must be used within WorkbenchProvider");
  return v;
}

