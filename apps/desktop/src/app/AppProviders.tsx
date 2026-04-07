import React from "react";
import { WizardProvider } from "./contexts/WizardContext";
import { ModeSelectProvider } from "./contexts/ModeSelectContext";
import { WorkbenchProvider } from "./contexts/WorkbenchContext";
import { HistoryProvider } from "./contexts/HistoryContext";
import { RulesCenterProvider } from "./contexts/RulesCenterContext";
import { AdvancedProvider } from "./contexts/AdvancedContext";
import { SystemProvider } from "./contexts/SystemContext";

export function AppProviders(props: {
  values: {
    wizard: any;
    modeSelect: any;
    workbench: any;
    history: any;
    rulesCenter: any;
    advanced: any;
    system: any;
  };
  children: React.ReactNode;
}) {
  const v = props.values;
  return (
    <WizardProvider value={v.wizard}>
      <ModeSelectProvider value={v.modeSelect}>
        <WorkbenchProvider value={v.workbench}>
          <HistoryProvider value={v.history}>
            <RulesCenterProvider value={v.rulesCenter}>
              <AdvancedProvider value={v.advanced}>
                <SystemProvider value={v.system}>{props.children}</SystemProvider>
              </AdvancedProvider>
            </RulesCenterProvider>
          </HistoryProvider>
        </WorkbenchProvider>
      </ModeSelectProvider>
    </WizardProvider>
  );
}

