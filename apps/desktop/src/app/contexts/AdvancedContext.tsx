import React, { createContext, useContext } from "react";

export type AdvancedContextValue = any;

const AdvancedContext = createContext<AdvancedContextValue | null>(null);

export function AdvancedProvider(props: { value: AdvancedContextValue; children: React.ReactNode }) {
  return <AdvancedContext.Provider value={props.value}>{props.children}</AdvancedContext.Provider>;
}

export function useAdvancedCtx(): AdvancedContextValue {
  const v = useContext(AdvancedContext);
  if (!v) throw new Error("useAdvancedCtx must be used within AdvancedProvider");
  return v;
}

