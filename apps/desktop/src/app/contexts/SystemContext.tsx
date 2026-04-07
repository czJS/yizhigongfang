import React, { createContext, useContext } from "react";

export type SystemContextValue = any;

const SystemContext = createContext<SystemContextValue | null>(null);

export function SystemProvider(props: { value: SystemContextValue; children: React.ReactNode }) {
  return <SystemContext.Provider value={props.value}>{props.children}</SystemContext.Provider>;
}

export function useSystemCtx(): SystemContextValue {
  const v = useContext(SystemContext);
  if (!v) throw new Error("useSystemCtx must be used within SystemProvider");
  return v;
}

