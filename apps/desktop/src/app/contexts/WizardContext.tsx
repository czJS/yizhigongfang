import React, { createContext, useContext } from "react";

export type WizardContextValue = any;

const WizardContext = createContext<WizardContextValue | null>(null);

export function WizardProvider(props: { value: WizardContextValue; children: React.ReactNode }) {
  return <WizardContext.Provider value={props.value}>{props.children}</WizardContext.Provider>;
}

export function useWizardCtx(): WizardContextValue {
  const v = useContext(WizardContext);
  if (!v) throw new Error("useWizardCtx must be used within WizardProvider");
  return v;
}

