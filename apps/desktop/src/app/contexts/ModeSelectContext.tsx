import React, { createContext, useContext } from "react";
import type { BatchModel } from "../../batchTypes";
import type { UiPrefs } from "../../batchStorage";
import type { AppConfig } from "../../types";

export type ModeSelectContextValue = {
  availableModes: string[];
  mode: BatchModel["mode"];
  config: AppConfig | null;
  uiPrefs: UiPrefs;
  setMode: (m: BatchModel["mode"]) => void;
  setUiPrefs: React.Dispatch<React.SetStateAction<UiPrefs>>;
  saveUiPrefs: (prefs: UiPrefs) => void;
};

const ModeSelectContext = createContext<ModeSelectContextValue | null>(null);

export function ModeSelectProvider(props: { value: ModeSelectContextValue; children: React.ReactNode }) {
  return <ModeSelectContext.Provider value={props.value}>{props.children}</ModeSelectContext.Provider>;
}

export function useModeSelectCtx(): ModeSelectContextValue {
  const v = useContext(ModeSelectContext);
  if (!v) throw new Error("useModeSelectCtx must be used within ModeSelectProvider");
  return v;
}

