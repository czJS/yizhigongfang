import { useCallback, useRef } from "react";
import { message } from "antd";
import { getConfig, getHardware, getHealth } from "../services/systemApi";
import type { AppConfig, HardwareInfo, Tier } from "../types";
import { loadUiPrefs } from "../batchStorage";

export function useBootstrap(opts: {
  form: { setFieldsValue: (vals: any) => void };
  refreshRulesTemplates?: () => void;
  setUiPrefs: (prefs: any) => void;
  setConfig: (cfg: AppConfig) => void;
  setHardware: (hw: HardwareInfo) => void;
  setHealth: (h: string) => void;
  setAvailableModes: (modes: string[]) => void;
  setMode: (m: any) => void;
  setPreset: (p: string) => void;
  setLoadingBoot: (v: boolean) => void;
}) {
  const bootstrapRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bootstrapRetryUntilRef = useRef<number>(0);
  const bootstrapRetryNotifiedRef = useRef<boolean>(false);

  const cleanup = useCallback(() => {
    if (bootstrapRetryTimer.current) {
      clearTimeout(bootstrapRetryTimer.current);
      bootstrapRetryTimer.current = null;
    }
    bootstrapRetryUntilRef.current = 0;
    bootstrapRetryNotifiedRef.current = false;
  }, []);

  const bootstrap = useCallback(async () => {
    opts.setLoadingBoot(true);
    try {
      const prefs = loadUiPrefs();
      opts.setUiPrefs(prefs);
      // On cold start, the main process may still be bringing up the backend.
      // Retry briefly to avoid "stuck in lite-only" until manual refresh.
      let cfg: AppConfig;
      let hw: HardwareInfo;
      let h: string;
      const maxAttempts = 50; // ~45s (packaged backend cold start can be slow on some machines)
      let lastErr: any = null;
      for (let i = 0; i < maxAttempts; i++) {
        try {
          // eslint-disable-next-line no-await-in-loop
          [cfg, hw, h] = await Promise.all([getConfig(), getHardware(), getHealth()]);
          lastErr = null;
          break;
        } catch (e: any) {
          lastErr = e;
          // eslint-disable-next-line no-await-in-loop
          await new Promise((r) => setTimeout(r, 900));
        }
      }
      if (lastErr) throw lastErr;
      opts.setConfig(cfg);
      opts.setHardware(hw);
      opts.setHealth(h);
      // best-effort: load rules templates list for wizard UI
      opts.refreshRulesTemplates?.();

      const qualityOnly = Boolean((cfg as any)?.ui?.quality_only);
      const qualityTeaserOnly = Boolean((cfg as any)?.ui?.quality_teaser_only);
      const onlineDisabled = Boolean((cfg as any)?.ui?.online_disabled);
      const modes =
        qualityOnly ? ["quality"] : cfg.available_modes && cfg.available_modes.length > 0 ? cfg.available_modes : ["lite"];
      opts.setAvailableModes(modes);
      const serverDefaultMode = (cfg as any).default_mode || (cfg.defaults as any)?.default_mode;
      const preferredMode =
        (qualityTeaserOnly && prefs.defaultMode === "quality") || (onlineDisabled && prefs.defaultMode === "online")
          ? "lite"
          : prefs.defaultMode;
      const normalizedServerDefaultMode =
        (qualityTeaserOnly && serverDefaultMode === "quality") || (onlineDisabled && serverDefaultMode === "online")
          ? "lite"
          : serverDefaultMode;
      const pickedMode =
        qualityOnly
          ? "quality"
          : preferredMode && modes.includes(preferredMode)
          ? preferredMode
          : modes.includes(normalizedServerDefaultMode as any)
            ? normalizedServerDefaultMode
            : (modes[0] || "lite");
      opts.setMode(pickedMode as any);

      // recommended preset from hardware tier
      const tier = (hw.tier as Tier) || "normal";
      const presetGuess = cfg.presets?.[tier] ? tier : "normal";
      const pickedPreset = prefs.defaultPreset && cfg.presets?.[prefs.defaultPreset] ? prefs.defaultPreset : presetGuess;
      opts.setPreset(pickedPreset);

      const merged = { ...(cfg.defaults || {}), ...(cfg.presets?.[pickedPreset] || {}) };
      const toggles = prefs.defaultToggles || {};
      const params = prefs.defaultParams || {};
      opts.form.setFieldsValue(merged);
      // Apply preferred toggles (if present)
      for (const [k, v] of Object.entries(toggles)) {
        if (typeof v === "boolean") {
          opts.form.setFieldsValue({ [k]: v });
        }
      }
      // Apply preferred scalar params (if present)
      for (const [k, v] of Object.entries(params)) {
        if (typeof v === "number" || typeof v === "string") {
          opts.form.setFieldsValue({ [k]: v });
        }
      }
    } catch (err: any) {
      const msg = String(err?.message || "初始化失败");
      message.error(msg);
      // Background retry: packaged backend cold start / model checks may take longer than a single attempt window.
      const now = Date.now();
      if (!bootstrapRetryUntilRef.current) {
        bootstrapRetryUntilRef.current = now + 180_000; // retry up to 3 minutes
      }
      if (now < bootstrapRetryUntilRef.current) {
        if (!bootstrapRetryNotifiedRef.current) {
          bootstrapRetryNotifiedRef.current = true;
          message.warning("后端可能仍在启动中，系统将自动重试初始化（无需手动点击“重新检测”）。");
        }
        if (!bootstrapRetryTimer.current) {
          bootstrapRetryTimer.current = setTimeout(() => {
            bootstrapRetryTimer.current = null;
            bootstrap();
          }, 2500);
        }
      }
    } finally {
      opts.setLoadingBoot(false);
    }
  }, [opts]);

  return { bootstrap, cleanup };
}

