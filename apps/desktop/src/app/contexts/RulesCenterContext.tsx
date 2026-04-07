import React, { createContext, useContext } from "react";

export type RulesCenterContextValue = any;

const RulesCenterContext = createContext<RulesCenterContextValue | null>(null);

export function RulesCenterProvider(props: { value: RulesCenterContextValue; children: React.ReactNode }) {
  return <RulesCenterContext.Provider value={props.value}>{props.children}</RulesCenterContext.Provider>;
}

export function useRulesCenterCtx(): RulesCenterContextValue {
  const v = useContext(RulesCenterContext);
  if (!v) throw new Error("useRulesCenterCtx must be used within RulesCenterProvider");
  return v;
}

