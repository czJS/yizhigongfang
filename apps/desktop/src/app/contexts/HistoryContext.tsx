import React, { createContext, useContext } from "react";

export type HistoryContextValue = any;

const HistoryContext = createContext<HistoryContextValue | null>(null);

export function HistoryProvider(props: { value: HistoryContextValue; children: React.ReactNode }) {
  return <HistoryContext.Provider value={props.value}>{props.children}</HistoryContext.Provider>;
}

export function useHistoryCtx(): HistoryContextValue {
  const v = useContext(HistoryContext);
  if (!v) throw new Error("useHistoryCtx must be used within HistoryProvider");
  return v;
}

