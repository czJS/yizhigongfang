import { useCallback, useEffect, useMemo, useState } from "react";
import { message } from "antd";

type RuntimeStatus = {
  packagedWindowsProduct: boolean;
  manifestPath?: string;
  manifestExists?: boolean;
  backendExe: string;
  backendExeExists: boolean;
  qualityWorkerExe: string;
  qualityWorkerExeExists: boolean;
  packagedConfigPath: string;
  packagedConfigExists: boolean;
  modelsPackZip: string;
  modelsPackZipExists: boolean;
  ollamaPackZip: string;
  ollamaPackZipExists: boolean;
  ollamaRoot: string;
  ollamaExe: string;
  ollamaExeExists: boolean;
  ollamaModelsRoot: string;
  runtimeChecks?: { key?: string; label?: string; path?: string; exists?: boolean }[];
};

export function useLocalPacksGate(opts: { requireLocalPacks: boolean }) {
  const { requireLocalPacks } = opts;

  const [modelsReady, setModelsReady] = useState(true);
  const [modelsRoot, setModelsRoot] = useState("");
  const [modelsZipHint, setModelsZipHint] = useState("");
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState("");
  const [modelsMissing, setModelsMissing] = useState<{ key?: string; label?: string; path?: string }[]>([]);
  const [modelsImporting, setModelsImporting] = useState(false);

  const [ollamaReady, setOllamaReady] = useState(false);
  const [ollamaPortOpen, setOllamaPortOpen] = useState(false);
  const [ollamaRoot, setOllamaRoot] = useState("");
  const [ollamaModelsRoot, setOllamaModelsRoot] = useState("");
  const [ollamaZipHint, setOllamaZipHint] = useState("");
  const [ollamaProcessorSummary, setOllamaProcessorSummary] = useState("");
  const [ollamaAcceleration, setOllamaAcceleration] = useState("idle");
  const [ollamaActiveModels, setOllamaActiveModels] = useState<string[]>([]);
  const [ollamaUsesGpu, setOllamaUsesGpu] = useState(false);
  const [ollamaLoading, setOllamaLoading] = useState(true);
  const [ollamaError, setOllamaError] = useState("");
  const [ollamaImporting, setOllamaImporting] = useState(false);
  const [ollamaStarting, setOllamaStarting] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  const [runtimeError, setRuntimeError] = useState("");
  const [backendRestarting, setBackendRestarting] = useState(false);

  const missingLabels = useMemo(() => {
    return (modelsMissing || []).map((m) => m.label || m.key || "unknown").filter(Boolean);
  }, [modelsMissing]);

  const refreshModelsStatus = useCallback(async () => {
    setModelsError("");
    setModelsLoading(true);
    try {
      // Non-packaged / non-Windows: models are managed by backend; skip local pack checks.
      if (!requireLocalPacks || !window.bridge?.getModelStatus) {
        setModelsReady(true);
        setModelsRoot("");
        setModelsZipHint("");
        setModelsMissing([]);
        return;
      }
      const st = await window.bridge.getModelStatus();
      setModelsReady(!!st?.ready);
      setModelsRoot(String(st?.root || ""));
      setModelsZipHint(String(st?.zipHint || st?.zip || ""));
      setModelsMissing(Array.isArray(st?.missing) ? st.missing : []);
    } catch (err: any) {
      setModelsReady(false);
      setModelsError(String(err?.message || "模型检测失败"));
    } finally {
      setModelsLoading(false);
    }
  }, [requireLocalPacks]);

  useEffect(() => {
    refreshModelsStatus();
  }, [refreshModelsStatus]);

  const refreshOllamaStatus = useCallback(async () => {
    setOllamaError("");
    setOllamaLoading(true);
    try {
      if (!window.bridge?.getOllamaStatus) {
        setOllamaReady(true);
        setOllamaPortOpen(!requireLocalPacks);
        setOllamaRoot("");
        setOllamaModelsRoot("");
        setOllamaZipHint("");
        setOllamaProcessorSummary("");
        setOllamaAcceleration("idle");
        setOllamaActiveModels([]);
        setOllamaUsesGpu(false);
        return;
      }
      const st = await window.bridge.getOllamaStatus();
      setOllamaReady(!!st?.ready);
      setOllamaPortOpen(!!st?.portOpen);
      setOllamaRoot(String(st?.root || ""));
      setOllamaModelsRoot(String(st?.modelsRoot || ""));
      setOllamaZipHint(String(st?.zipHint || st?.zip || ""));
      setOllamaProcessorSummary(String(st?.processorSummary || ""));
      setOllamaAcceleration(String(st?.acceleration || "idle"));
      setOllamaActiveModels(Array.isArray(st?.activeModels) ? st.activeModels.map((v) => String(v)) : []);
      setOllamaUsesGpu(!!st?.usesGpu);
      const missingModels = Array.isArray(st?.missingModels) ? st.missingModels.filter(Boolean) : [];
      if (missingModels.length > 0) {
        setOllamaError(`缺少 Ollama 模型：${missingModels.join("、")}`);
      }
    } catch (err: any) {
      setOllamaReady(false);
      setOllamaError(String(err?.message || "Ollama 状态检测失败"));
    } finally {
      setOllamaLoading(false);
    }
  }, [requireLocalPacks]);

  useEffect(() => {
    refreshOllamaStatus();
  }, [refreshOllamaStatus]);

  const refreshRuntimeStatus = useCallback(async () => {
    setRuntimeError("");
    setRuntimeLoading(true);
    try {
      if (!requireLocalPacks || !window.bridge?.getRuntimeStatus) {
        setRuntimeStatus(null);
        return;
      }
      const st = await window.bridge.getRuntimeStatus();
      setRuntimeStatus(st || null);
    } catch (err: any) {
      setRuntimeStatus(null);
      setRuntimeError(String(err?.message || "运行时状态检测失败"));
    } finally {
      setRuntimeLoading(false);
    }
  }, [requireLocalPacks]);

  useEffect(() => {
    refreshRuntimeStatus();
  }, [refreshRuntimeStatus]);

  const handleRestartBackend = useCallback(async () => {
    setRuntimeError("");
    setBackendRestarting(true);
    const msgKey = "backend-restart";
    message.loading({ content: "正在重启本地 backend...", key: msgKey, duration: 0 });
    try {
      if (!window.bridge?.restartBackend) {
        const msg = "未连接到主进程，无法重启 backend。";
        setRuntimeError(msg);
        message.error({ content: msg, key: msgKey });
        return;
      }
      const res = await window.bridge.restartBackend();
      await refreshRuntimeStatus();
      if (!res?.ok) {
        const msg = String(res?.error || "重启 backend 失败");
        setRuntimeError(msg);
        message.error({ content: msg, key: msgKey });
        return;
      }
      message.success({ content: "本地 backend 已重启", key: msgKey });
    } catch (err: any) {
      const msg = String(err?.message || err || "重启 backend 失败");
      setRuntimeError(msg);
      message.error({ content: msg, key: msgKey });
    } finally {
      setBackendRestarting(false);
    }
  }, [refreshRuntimeStatus]);

  const handleExtractModels = useCallback(
    async (zipPath?: string) => {
      setModelsError("");
      setModelsImporting(true);
      const msgKey = "model-import";
      message.loading({ content: "正在导入模型包（解压中）...", key: msgKey, duration: 0 });
      if (!window.bridge?.extractModelPack) {
        setModelsError("未连接到主进程，无法导入模型包。");
        message.error({ content: "未连接到主进程，无法导入模型包。", key: msgKey });
        setModelsImporting(false);
        return;
      }
      if (!zipPath) {
        setModelsError("请先选择模型包（models_pack.zip）。");
        message.warning({ content: "请先选择模型包（models_pack.zip）。", key: msgKey });
        setModelsImporting(false);
        return;
      }
      try {
        const res = await window.bridge.extractModelPack(zipPath);
        if (!res?.ok) {
          setModelsError(res?.error || "导入失败，请检查 models_pack.zip 是否完整");
          message.error({ content: res?.error || "导入失败，请检查 models_pack.zip 是否完整", key: msgKey });
          return;
        }
        await refreshModelsStatus();
        await refreshRuntimeStatus();
        if (Array.isArray(res?.missing) && res.missing.length > 0) {
          const labels = (res.missing || []).map((m: any) => m?.label || m?.key).filter(Boolean);
          message.warning({ content: `导入完成，但仍缺少：${labels.join("、") || "部分模型"}。请确认模型包完整。`, key: msgKey });
        } else {
          message.success({ content: "模型导入成功（已就绪）", key: msgKey });
        }
      } catch (err: any) {
        const msg = String(err?.message || err || "导入失败");
        setModelsError(msg);
        message.error({ content: msg, key: msgKey });
      } finally {
        setModelsImporting(false);
      }
    },
    [refreshModelsStatus, refreshRuntimeStatus],
  );

  const handlePickModels = useCallback(async () => {
    if (!window.bridge?.pickModelPack) {
      setModelsError("未连接到主进程，无法选择模型包。");
      return;
    }
    const p = await window.bridge.pickModelPack();
    if (!p) return;
    await handleExtractModels(p);
  }, [handleExtractModels]);

  const handleExtractOllama = useCallback(
    async (zipPath?: string) => {
      setOllamaError("");
      setOllamaImporting(true);
      const msgKey = "ollama-import";
      message.loading({ content: "正在导入 Ollama 包（解压中）...", key: msgKey, duration: 0 });
      if (!window.bridge?.extractOllamaPack) {
        setOllamaError("未连接到主进程，无法导入 Ollama 包。");
        message.error({ content: "未连接到主进程，无法导入 Ollama 包。", key: msgKey });
        setOllamaImporting(false);
        return;
      }
      if (!zipPath) {
        setOllamaError("请先选择 Ollama 包（ollama_pack.zip）。");
        message.warning({ content: "请先选择 Ollama 包（ollama_pack.zip）。", key: msgKey });
        setOllamaImporting(false);
        return;
      }
      try {
        const res = await window.bridge.extractOllamaPack(zipPath);
        if (!res?.ok) {
          setOllamaError(res?.error || "导入失败，请检查 ollama_pack.zip 是否完整");
          message.error({ content: res?.error || "导入失败，请检查 ollama_pack.zip 是否完整", key: msgKey });
          return;
        }
        await refreshOllamaStatus();
        await refreshRuntimeStatus();
        message.success({ content: "Ollama 包导入完成", key: msgKey });
      } catch (err: any) {
        const msg = String(err?.message || err || "导入失败");
        setOllamaError(msg);
        message.error({ content: msg, key: msgKey });
      } finally {
        setOllamaImporting(false);
      }
    },
    [refreshOllamaStatus, refreshRuntimeStatus],
  );

  const handlePickOllama = useCallback(async () => {
    if (!window.bridge?.pickOllamaPack) {
      setOllamaError("未连接到主进程，无法选择 Ollama 包。");
      return;
    }
    const p = await window.bridge.pickOllamaPack();
    if (!p) return;
    await handleExtractOllama(p);
  }, [handleExtractOllama]);

  const handleEnsureOllama = useCallback(async () => {
    setOllamaError("");
    setOllamaStarting(true);
    const msgKey = "ollama-start";
    message.loading({ content: "正在启动本地 Ollama...", key: msgKey, duration: 0 });
    try {
      if (!window.bridge?.ensureOllama) {
        const msg = "未连接到主进程，无法启动 Ollama。";
        setOllamaError(msg);
        message.error({ content: msg, key: msgKey });
        return;
      }
      const res = await window.bridge.ensureOllama();
      await refreshOllamaStatus();
      await refreshRuntimeStatus();
      if (!res?.ok) {
        const msg = String(res?.error || "启动 Ollama 失败");
        setOllamaError(msg);
        message.error({ content: msg, key: msgKey });
        return;
      }
      message.success({ content: "本地 Ollama 已启动", key: msgKey });
    } catch (err: any) {
      const msg = String(err?.message || err || "启动 Ollama 失败");
      setOllamaError(msg);
      message.error({ content: msg, key: msgKey });
    } finally {
      setOllamaStarting(false);
    }
  }, [refreshOllamaStatus, refreshRuntimeStatus]);

  return {
    // models pack
    modelsReady,
    modelsRoot,
    modelsZipHint,
    modelsLoading,
    modelsError,
    modelsMissing,
    missingLabels,
    modelsImporting,
    handlePickModels,
    refreshModelsStatus,
    // ollama pack
    ollamaReady,
    ollamaPortOpen,
    ollamaRoot,
    ollamaModelsRoot,
    ollamaZipHint,
    ollamaProcessorSummary,
    ollamaAcceleration,
    ollamaActiveModels,
    ollamaUsesGpu,
    ollamaLoading,
    ollamaError,
    ollamaImporting,
    ollamaStarting,
    handlePickOllama,
    handleEnsureOllama,
    refreshOllamaStatus,
    runtimeStatus,
    runtimeLoading,
    runtimeError,
    backendRestarting,
    refreshRuntimeStatus,
    handleRestartBackend,
  };
}

