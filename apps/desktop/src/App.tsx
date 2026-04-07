import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Menu,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Select,
  Slider,
  Row,
  Space,
  Steps,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadRequestOption as RcCustomRequestOptions } from "rc-upload/lib/interface";
import {
  AppstoreOutlined,
  CloudOutlined,
  CrownOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  HistoryOutlined,
  InboxOutlined,
  KeyOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RocketOutlined,
  ReloadOutlined,
  SettingOutlined,
  StopOutlined,
  ThunderboltOutlined,
  QuestionCircleOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { apiBase } from "./services/systemApi";
import { cancelTask, cleanupTaskArtifacts, getArtifacts, getLog, startTask } from "./services/taskApi";
import type { AppConfig, HardwareInfo, QualityReport, RulesetDoc, RulesetTemplateInfo, TaskStatus, Tier } from "./types";
import type { BatchModel, BatchTask, UiTaskState } from "./batchTypes";
import { loadUiPrefs, saveUiPrefs, type UiPrefs } from "./batchStorage";
import { defaultBatchName, nowTs, safeStem, twoDigitIndex } from "./utils";
import {
  createId,
  downloadTextFile,
  issueTag,
  joinList,
  normalizeLegacyQualityIssueText,
  qualityExampleGroups,
  shortReason,
  splitList,
  suggestForIssue,
  tagColorForUiState,
  uiStateFromBackend,
} from "./app/appHelpers";
import { batchStateLabel, modeLabel, taskStateLabel } from "./app/labels";
import { paramMeta, riskTagColor, stageOfToggle, textMeta, toggleMeta } from "./app/advancedMeta";
import { findBatchIdWithRunningTask } from "./app/queueHelpers";
import { TaskDrawerContent } from "./components/taskDrawer/TaskDrawerContent";
import { GlossaryModal } from "./components/modals/GlossaryModal";
import { CleanupArtifactsModal } from "./components/modals/CleanupArtifactsModal";
import { EraseSubOverrideModal } from "./components/modals/EraseSubOverrideModal";
import { RegionPickerModal } from "./components/modals/RegionPickerModal";
import { AppFrame } from "./components/AppFrame";
import { AppOverlays } from "./components/AppOverlays";
import { AppProviders } from "./app/AppProviders";
import { createAppProviderValues } from "./app/createAppProviderValues";
import { HistoryScreen } from "./screens/HistoryScreen";
import { RulesCenterScreen } from "./screens/RulesCenterScreen";
import { AdvancedScreen } from "./screens/AdvancedSettingsScreen";
import { SystemScreen } from "./screens/SystemScreen";
import { WorkbenchScreen } from "./screens/WorkbenchScreen";
import { WizardScreen } from "./screens/WizardScreen";
import { ModeSelectScreen } from "./screens/ModeSelectScreen";
import { usePersistedBatches } from "./hooks/usePersistedBatches";
import { useQueueScheduler } from "./hooks/useQueueScheduler";
import { ModeSelectProvider } from "./app/contexts/ModeSelectContext";
import { WizardProvider } from "./app/contexts/WizardContext";
import { WorkbenchProvider } from "./app/contexts/WorkbenchContext";
import { HistoryProvider } from "./app/contexts/HistoryContext";
import { SystemProvider } from "./app/contexts/SystemContext";
import { AdvancedProvider } from "./app/contexts/AdvancedContext";
import { RulesCenterProvider } from "./app/contexts/RulesCenterContext";
import { useBootstrap } from "./hooks/useBootstrap";
import { useTaskPolling } from "./hooks/useBatchQueue";
import { useRegionPicker } from "./hooks/useRegionPicker";
import { useBatchRunner } from "./hooks/useBatchRunner";
import { useWizardBatchCreation } from "./hooks/useWizardBatchCreation";
import { useDelivery } from "./hooks/useDelivery";
import { useDiagnosticsZip } from "./hooks/useDiagnosticsZip";
import { useGlossaryModal } from "./hooks/useGlossaryModal";
import { useRulesCenterModel } from "./hooks/useRulesCenterModel";
import { useLocalPacksGate } from "./hooks/useLocalPacksGate";
import { useCloudAuthGate } from "./hooks/useCloudAuthGate";
import { useTaskDrawer } from "./hooks/useTaskDrawer";
import { useTaskActions } from "./hooks/useTaskActions";
import { useTaskFinalize } from "./hooks/useTaskFinalize";
import { useRegionPickerController } from "./hooks/useRegionPickerController";
import { useSubtitleSettings, type SavedSubtitleSettings } from "./hooks/useSubtitleSettings";
import { filterLiteFastParams, normalizePerTaskOverrideValues, pickPerTaskOverrideValues } from "./app/domains/params/batchParams";
import { usePerTaskOverrides } from "./hooks/usePerTaskOverrides";

const { Content, Sider } = Layout;
const { Title, Paragraph, Text } = Typography;
const { Option } = Select;

const DEFAULT_POLL_MS = 1200;
const App: React.FC = () => {
  // 第一期开箱即用产品口径只收敛 Windows 打包态。
  // 不再依赖 userAgent 猜平台，而是直接读取主进程暴露的运行时信息。
  const hasBridge = typeof window !== "undefined" && !!(window as any).bridge;
  const runtimeInfo = hasBridge ? (window as any).bridge?.runtimeInfo : null;
  const requireLocalPacks = Boolean(runtimeInfo?.windowsPackagedProduct);
  const cloudAuth = useCloudAuthGate();
  const taskCreationBlocked = Boolean(cloudAuth.taskCreationBlocked);
  const taskCreationBlockReason = String(cloudAuth.taskCreationBlockedReason || "");

  const localPacks = useLocalPacksGate({
    requireLocalPacks,
  });
  const {
    modelsReady,
    modelsRoot,
    modelsZipHint,
    modelsLoading,
    modelsError,
    modelsImporting,
    missingLabels,
    handlePickModels,
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
    refreshRuntimeStatus,
  } = localPacks;
  const [health, setHealth] = useState<string>("unknown");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);
  const [loadingBoot, setLoadingBoot] = useState(false);

  const [route, setRoute] = useState<"wizard" | "workbench" | "history" | "mode" | "rules" | "advanced" | "system">("wizard");
  const [renewalModalOpen, setRenewalModalOpen] = useState(false);
  const [uiPrefs, setUiPrefs] = useState<UiPrefs>({});
  const uiPrefsRef = useRef<UiPrefs>({});
  const [wizardStep, setWizardStep] = useState(0);
  const [subtitleSource, setSubtitleSource] = useState<"has" | "none">("has");
  const [reviewEnabled, setReviewEnabled] = useState(true);
  const [regionPickerPreviewSource, setRegionPickerPreviewSource] = useState("");
  const glossary = useGlossaryModal();

  const rulesCenterModel = useRulesCenterModel();
  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false);
  const [cleanupTaskIndex, setCleanupTaskIndex] = useState(-1);
  const [cleanupIncludeDiagnostics, setCleanupIncludeDiagnostics] = useState(true);
  const [cleanupIncludeResume, setCleanupIncludeResume] = useState(false);
  const [cleanupIncludeReview, setCleanupIncludeReview] = useState(false);
  // SavedSubtitleSettings is managed by useSubtitleSettings
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [wizardTasks, setWizardTasks] = useState<
    { inputName: string; inputPath: string; localPath?: string; overrides?: Record<string, any> }[]
  >([]);
  const [wizardUploading, setWizardUploading] = useState(false);
  // per-task override editor state is initialized later (needs form + batchesRef)

  // Visual region picker (for erase_subtitle coords)
  const [regionPickerOpen, setRegionPickerOpen] = useState(false);
  const [regionPickerVideoPath, setRegionPickerVideoPath] = useState<string>("");
  const [regionPickerTarget, setRegionPickerTarget] = useState<"batch" | "override">("batch");
  const [regionPickerPurpose, setRegionPickerPurpose] = useState<"erase" | "subtitle">("erase");
  const {
    regionPickerRect,
    setRegionPickerRect,
    setRegionRectSafe,
    regionPickerSampleFontSize,
    setRegionPickerSampleFontSize,
    regionPickerSampleText,
    setRegionPickerSampleText,
  } = useRegionPicker();
  const [regionPickerVideoReady, setRegionPickerVideoReady] = useState(false);
  const [regionPickerVideoError, setRegionPickerVideoError] = useState<string>("");
  const [regionPickerVideoInfo, setRegionPickerVideoInfo] = useState<{ name?: string; localPath?: string; duration?: number; w?: number; h?: number }>({});
  const regionPickerVideoRef = useRef<HTMLVideoElement | null>(null);
  const regionPickerFrameRef = useRef<HTMLDivElement | null>(null);
  const regionPickerFileInputRef = useRef<HTMLInputElement | null>(null);
  // Preview scale: map ASS/视频原始字号到当前预览窗口的 CSS 像素，保证“设置页预览 ≈ 成片效果（在同等缩放下）”
  const [regionPickerVideoScale, setRegionPickerVideoScale] = useState<number>(1);
  const [regionPickerVideoBox, setRegionPickerVideoBox] = useState<{ w: number; h: number; x: number; y: number }>({
    w: 0,
    h: 0,
    x: 0,
    y: 0,
  });

  const regionPickerActive = regionPickerOpen || (route === "wizard" && wizardStep === 1 && subtitleSource === "has");
  const { handleRegionPickerFileChange, resetRegionPickerVideo } = useRegionPickerController({
    regionPickerActive,
    regionPickerVideoRef,
    regionPickerFrameRef,
    regionPickerVideoPath,
    regionPickerVideoReady,
    regionPickerVideoInfo,
    setRegionPickerVideoScale,
    setRegionPickerVideoBox,
    route,
    wizardStep,
    subtitleSource,
    regionPickerPurpose,
    setRegionPickerPurpose,
    regionPickerTarget,
    setRegionPickerTarget,
    toFileUrl,
    setRegionPickerVideoPath,
    setRegionPickerVideoReady,
    setRegionPickerVideoError,
    setRegionPickerVideoInfo,
  });

  const [form] = Form.useForm();

  const subtitleSettings = useSubtitleSettings({
    route,
    wizardStep,
    subtitleSource,
    setSubtitleSource,
    wizardTasks: wizardTasks as any,
    form,
    regionPickerRect,
    setRegionPickerRect,
    regionPickerSampleFontSize,
    setRegionPickerSampleFontSize,
    setFinalSubtitleFontSize,
    toFileUrl,
    regionPickerPreviewSource,
    setRegionPickerPreviewSource,
    regionPickerVideoPath,
    setRegionPickerVideoPath,
    setRegionPickerVideoReady,
    setRegionPickerVideoError,
    setRegionPickerVideoInfo,
    resetRegionPickerVideo,
  });

  const { savedSubtitleSettings, savedSubtitleSettingsRef, saveSubtitleSettings, hasUnsavedHasSettings, hasUnsavedNoneSettings, applySavedSubtitleSettings } =
    subtitleSettings;
  const [mode, setMode] = useState<BatchModel["mode"]>("lite");
  const [availableModes, setAvailableModes] = useState<string[]>(["lite"]);
  const [preset, setPreset] = useState<string>("normal");
  const [batchName, setBatchName] = useState<string>(defaultBatchName());
  const [outputDir, setOutputDir] = useState<string>("");
  const qualityTeaserOnly = Boolean((config as any)?.ui?.quality_teaser_only);
  const onlineDisabled = Boolean((config as any)?.ui?.online_disabled);

  const { batches, setBatches, activeBatchId, setActiveBatchId, batchesRef, activeBatchIdRef } = usePersistedBatches({
    onRestoreActiveBatch: () => setRoute("workbench"),
  });
  const activeBatch = useMemo(() => batches.find((b) => b.id === activeBatchId) || null, [batches, activeBatchId]);

  const overrides = usePerTaskOverrides({
    form,
    wizardTasks,
    setWizardTasks: setWizardTasks as any,
    batchesRef,
    setBatches,
  });
  const {
    overrideModalOpen,
    setOverrideModalOpen,
    overrideEditing,
    setOverrideEditing,
    overrideForm,
    currentOverrideLocalPath,
    openEraseSubOverrideEditor,
    applyEraseSubOverrideToWizard,
    applyEraseSubOverrideToBatch,
  } = overrides;

  const drawer = useTaskDrawer({ activeBatch, showTaskLogs: Boolean((uiPrefs as any)?.showTaskLogs) });
  const { drawerOpen, drawerTaskIndex, drawerInitialTab, drawerLog, drawerLogOffset, drawerLogLoading, drawerWidth, openTaskDrawer } = drawer;
  const [advancedShowAll, setAdvancedShowAll] = useState(false);

  const pollingMs = useMemo(() => config?.ui?.polling_ms || DEFAULT_POLL_MS, [config]);

  const isBuildDev = !!import.meta.env.DEV;
  const devToolsEnabled = uiPrefs.devToolsEnabled ?? isBuildDev;

  const tickGlobalQueueRef = useRef<() => void>(() => {});
  const finalizeTaskRef = useRef<(batchId: string, taskIdx: number, taskId: string, st: TaskStatus) => Promise<void>>(
    async () => {},
  );

  const { bootstrap, cleanup: cleanupBootstrap } = useBootstrap({
    form,
    refreshRulesTemplates: rulesCenterModel.refreshRulesTemplates,
    setUiPrefs,
    setConfig,
    setHardware,
    setHealth,
    setAvailableModes,
    setMode,
    setPreset,
    setLoadingBoot,
  });

  const { startPollingForTask, stopPolling, activeBackendTaskIdRef } = useTaskPolling({
    pollingMs,
    drawerOpen,
    drawerTaskIndex,
    drawerLogOffset,
    showTaskLogs: Boolean((uiPrefs as any)?.showTaskLogs),
    setDrawerLog: drawer.setDrawerLog as any,
    setDrawerLogOffset: drawer.setDrawerLogOffset,
    updateActiveBatchById: updateActiveBatchById as any,
    finalizeTask: (batchId: string, taskIdx: number, taskId: string, st: TaskStatus) => finalizeTaskRef.current(batchId, taskIdx, taskId, st),
  });

  useEffect(() => {
    if (activeBackendTaskIdRef.current) return;
    for (const batch of batches) {
      const runningIdx = batch.tasks.findIndex((t) => t.state === "running" && !!t.taskId);
      if (runningIdx >= 0) {
        const taskId = String(batch.tasks[runningIdx].taskId || "");
        if (taskId) {
          startPollingForTask(batch.id, runningIdx, taskId);
          return;
        }
      }
    }
  }, [batches, startPollingForTask, activeBackendTaskIdRef]);

  // When the user turns on "显示日志" while drawer is open, load a tail snapshot once.
  useEffect(() => {
    if (!drawerOpen) return;
    if (!Boolean((uiPrefs as any)?.showTaskLogs)) return;
    if (!activeBatch || drawerTaskIndex < 0) return;
    const t = activeBatch.tasks[drawerTaskIndex];
    if (!t?.taskId) return;
    if (drawerLog && drawerLog.length > 0) return;
    (async () => {
      try {
        const res = await getLog(t.taskId, -8000);
        drawer.setDrawerLog((prev: string) => (prev ? prev : res.content || ""));
        drawer.setDrawerLogOffset(res.next_offset || 0);
      } catch {
        // ignore
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawerOpen, drawerTaskIndex, activeBatch?.id, (uiPrefs as any)?.showTaskLogs]);

  const { startNextIfNeeded, updateBatchForTaskStart, updateBatchStateIfAllDone } = useBatchRunner({
    batchesRef,
    updateActiveBatchById: updateActiveBatchById as any,
    startPollingForTask,
    tickGlobalQueue: () => tickGlobalQueueRef.current?.(),
    getTaskCreationBlockReason: () => taskCreationBlockReason,
  });

  const { tickGlobalQueue, startQueue, pauseQueue, resumeQueue, pauseQueueById, resumeQueueById } = useQueueScheduler({
    batchesRef,
    setBatches,
    activeBatchId,
    setActiveBatchId,
    setRoute,
    updateActiveBatch,
    updateActiveBatchById,
    startNextIfNeeded,
    getTaskCreationBlockReason: () => taskCreationBlockReason,
  });

  tickGlobalQueueRef.current = tickGlobalQueue;

  const {
    getDefaultOutputsRoot,
    openDefaultOutputsFolder,
    openBatchOutputFolder,
    openDeliveredDirForTask,
    deliverTaskToOutputDir,
  } = useDelivery({
    batchesRef,
    updateActiveBatchById: updateActiveBatchById as any,
    openPath,
    uiPrefsRef,
  });

  const { exportDiagnosticZipForTask } = useDiagnosticsZip({
    activeBatch,
    getDefaultOutputsRoot,
    openPath,
  });

  useEffect(() => {
    uiPrefsRef.current = uiPrefs || {};
  }, [uiPrefs]);

  useEffect(() => {
            bootstrap();
    return () => {
      stopPolling();
      cleanupBootstrap();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateActiveBatch(updater: (b: BatchModel) => BatchModel) {
    setBatches((prev) => prev.map((b) => (b.id === activeBatchId ? updater(b) : b)));
  }

  function ensureTaskCreationAllowed(showMessage = true) {
    if (!taskCreationBlockReason) return true;
    if (showMessage) message.warning(taskCreationBlockReason);
    return false;
  }

  function openRenewalModal() {
    if (!cloudAuth.canRenewInApp) return;
    setRenewalModalOpen(true);
  }

  async function handleRenewalRedeem() {
    const ok = await cloudAuth.handleRedeemCode();
    if (ok) setRenewalModalOpen(false);
  }

  async function chooseOutputDir() {
    const picker = window.bridge?.selectDirectory;
    if (!picker) {
      message.info("当前环境无法弹出文件夹选择器（仅 Electron 桌面版支持）。你仍可继续并用“下载”方式取文件。");
      return;
    }
    const dir = await picker();
    if (dir) setOutputDir(dir);
  }

  async function openPath(p: string) {
    const open = window.bridge?.openPath;
    if (!open) {
      message.info("当前环境无法直接打开文件夹（仅 Electron 桌面版支持）。");
      return;
    }
    const res = await open(p);
    if (!res?.ok) {
      const err = res?.error || "打开失败";
      if (/ENOENT|no such file|not found|不存在|找不到/i.test(String(err))) {
        message.error("目标文件不存在");
      } else {
        message.error(err);
      }
    }
  }

  function toFileUrl(p: string): string {
    const s = String(p || "");
    if (!s) return "";
    if (s.startsWith("http://") || s.startsWith("https://") || s.startsWith("file://")) return s;
    // Electron dev: file:// is blocked under http origin; use custom localfile:// protocol.
    // Normalize Windows paths to file URL style (e.g. D:\a\b -> localfile:///D:/a/b).
    let normalized = s.replace(/\\/g, "/");
    if (/^[A-Za-z]\//.test(normalized) && !/^[A-Za-z]:\//.test(normalized)) {
      normalized = `${normalized[0]}:/${normalized.slice(2)}`;
    }
    if (/^[A-Za-z]:\//.test(normalized)) {
      return `localfile:///${encodeURI(normalized)}`;
    }
    // Expect absolute paths like /Users/... or UNC paths like //server/share/...
    return `localfile://${encodeURI(normalized)}`;
  }

  function openRegionPicker(target: "batch" | "override", localVideoPath: string) {
    // default to erase; call openRegionPickerFor(...) to specify subtitle box
    openRegionPickerFor("erase", target, localVideoPath);
  }

  function openRegionPickerFor(purpose: "erase" | "subtitle", target: "batch" | "override", localVideoPath: string) {
    const src = toFileUrl(localVideoPath);
    const f = target === "batch" ? form : overrideForm;
    const vals = f.getFieldsValue(true) || {};
    // Default: start centered horizontally (x derived from w), bottom-ish.
    const w0 = Number(vals.erase_subtitle_w ?? 0.9);
    const x0 = (1 - (Number.isFinite(w0) ? w0 : 0.9)) / 2;
    const x = Number(vals.erase_subtitle_x ?? x0);
    const y = Number(vals.erase_subtitle_y ?? 0.745);
    const w = w0;
    const h = Number(vals.erase_subtitle_h ?? 0.22);
    setRegionPickerRect({
      x: Number.isFinite(x) ? x : 0.05,
      y: Number.isFinite(y) ? y : 0.745,
      w: Number.isFinite(w) ? w : 0.9,
      h: Number.isFinite(h) ? h : 0.22,
    });
    setRegionPickerTarget(target);
    setRegionPickerPurpose(purpose);
    setRegionPickerVideoPath(src || "");
    setRegionPickerVideoReady(false);
    setRegionPickerVideoError("");
    setRegionPickerVideoInfo({});
    {
      const fs = Number(vals?.sub_font_size ?? 34);
      setRegionPickerSampleFontSize(Number.isFinite(fs) ? Math.max(10, Math.min(60, fs)) : 34);
    }
    setRegionPickerOpen(true);
  }

  function regionPickerForm() {
    return regionPickerTarget === "batch" ? form : overrideForm;
  }

  function setFinalSubtitleFontSize(v: number) {
    const n = Number(v || 18);
    const clamped = Math.max(10, Math.min(60, n));
    setRegionPickerSampleFontSize(clamped);
    // This is the real font size used by final burn_subtitles.
    regionPickerForm().setFieldsValue({ sub_font_size: clamped });
  }

  const rulesCenter = <RulesCenterScreen />;
  function openCleanupDialog(taskIdx: number) {
    setCleanupTaskIndex(taskIdx);
    setCleanupIncludeDiagnostics(true);
    setCleanupIncludeResume(false);
    setCleanupIncludeReview(false);
    setCleanupDialogOpen(true);
  }

  async function confirmCleanupArtifacts() {
    const b = activeBatch;
    if (!b) return;
    const idx = cleanupTaskIndex;
    if (idx < 0 || idx >= b.tasks.length) return;
    const t = b.tasks[idx];
    if (!t.taskId) {
      message.error("该任务还没有 task_id");
      return;
    }
    try {
      message.loading({ content: "正在清理中间产物…", key: `cleanup_${t.taskId}`, duration: 0 });
      const res = await cleanupTaskArtifacts(t.taskId, {
        include_diagnostics: cleanupIncludeDiagnostics,
        include_resume: cleanupIncludeResume,
        include_review: cleanupIncludeReview,
      });
      const arts = await getArtifacts(t.taskId).catch(() => []);
      updateActiveBatch((bb) => {
        const tasks = [...bb.tasks];
        tasks[idx] = { ...tasks[idx], artifacts: arts };
        return { ...bb, tasks };
      });
      const removedCount = res?.removed?.length || 0;
      const errorCount = res?.errors?.length || 0;
      message.success({
        content: `清理完成：移除 ${removedCount} 项${errorCount ? `，失败 ${errorCount} 项` : ""}`,
        key: `cleanup_${t.taskId}`,
      });
    } catch (err: any) {
      message.error({ content: err?.message || "清理失败", key: `cleanup_${t.taskId}` });
    } finally {
      setCleanupDialogOpen(false);
    }
  }

  function resetWizard() {
    setWizardStep(0);
    setWizardTasks([]);
    setBatchName(defaultBatchName());
    setOutputDir("");
    setReviewEnabled(true);
    setRegionPickerPreviewSource("");
    rulesCenterModel.resetWizardRulesState();
    resetRegionPickerVideo();
    drawer.setDrawerOpen(false);
    drawer.setDrawerTaskIndex(-1);
    setRoute("wizard");
  }

  function openNewBatchWizard() {
    if (!ensureTaskCreationAllowed()) return;
    resetWizard();
  }

  function openQualityUpgradeWizardFromTask(task: { inputName: string; inputPath: string; localPath?: string }) {
    if (qualityTeaserOnly) {
      setRoute("mode");
      message.info("当前版本的质量模式仅展示介绍与升级入口，不会切入真实质量链路。");
      void task;
      return;
    }
    // Switch to quality mode and pre-fill the wizard with a single file.
    // We intentionally do NOT auto-start; user should confirm settings first.
    setMode("quality");
    try {
      const allow = allowedPresetKeysForMode("quality");
      const preferred = (uiPrefs?.defaultPreset || "").trim();
      const nextPreset = (preferred && allow.includes(preferred) ? preferred : allow.includes("quality") ? "quality" : allow[0]) || "quality";
      setPreset(nextPreset);
    } catch {
      // ignore
    }
    setWizardTasks([{ inputName: task.inputName, inputPath: task.inputPath, localPath: task.localPath || "", overrides: {} }]);
    setWizardStep(1); // jump to settings (file already selected)
    setRoute("wizard");
    message.info("已切换到质量模式，并载入当前文件。确认设置后开始处理。");
  }

  function presetLabel(key: string): string {
    if (key === "high") return "更清晰（高端）";
    if (key === "mid") return "更快（中端）";
    if (key === "normal") return "更省资源（普通）";
    return key;
  }

  const presetOptions = useMemo(() => {
    return Object.entries(config?.presets || {}).map(([k, v]) => ({
      key: k,
      label: presetLabel(k),
      hint: v.hardware_hint || "",
    }));
  }, [config]);

  function allowedPresetKeysForMode(m: BatchModel["mode"]): string[] {
    const all = new Set(Object.keys(config?.presets || {}));
    if (m === "quality") return ["quality"].filter((k) => all.has(k));
    if (m === "online") return ["online"].filter((k) => all.has(k));
    // lite
    const lite = ["normal", "mid", "high"].filter((k) => all.has(k));
    return lite.length ? lite : Array.from(all);
  }

  const allowedPresetOptions = useMemo(() => {
    const allow = new Set(allowedPresetKeysForMode(mode));
    return presetOptions.filter((p) => allow.has(p.key));
  }, [presetOptions, mode, config]);

  useEffect(() => {
    if (!qualityTeaserOnly) return;
    if (mode !== "quality") return;
    setMode("lite");
  }, [qualityTeaserOnly, mode]);

  useEffect(() => {
    if (!onlineDisabled) return;
    if (mode !== "online") return;
    setMode("lite");
  }, [onlineDisabled, mode]);

  // Online mode still does not expose rules center in the sidebar.
  useEffect(() => {
    if (mode === "online") {
      if (route === "rules") setRoute("wizard");
    }
  }, [mode, route]);

  // Ensure preset matches current mode (avoid mixing lite presets into quality mode).
  useEffect(() => {
    const allow = new Set(allowedPresetKeysForMode(mode));
    if (allow.size === 0) return;
    if (!allow.has(preset)) {
      const next = Array.from(allow)[0];
      setPreset(next);
    }
  }, [mode, config]);

  const { handleAddUpload, moveTask, removeTask, createBatchAndGo } = useWizardBatchCreation({
    wizardTasks,
    setWizardTasks,
    setWizardUploading,
      mode,
    outputDir,
    form: form as any,
    reviewEnabled,
    currentBatchRulesetOverride: rulesCenterModel.currentBatchRulesetOverride,
    savedSubtitleSettingsRef,
    savedSubtitleSettings,
    regionPickerRect,
    filterLiteFastParams,
      preset,
    batchName,
    setBatches,
    setActiveBatchId,
    setWizardStep,
    setBatchName,
    setRoute,
    startQueue,
    getTaskCreationBlockReason: () => taskCreationBlockReason,
  });

  useEffect(() => {
    const bridge = typeof window !== "undefined" ? (window as any).bridge : null;
    const e2eEnabled = Boolean(bridge?.runtimeInfo?.e2eEnabled && typeof bridge?.readFileForE2E === "function");
    if (!e2eEnabled) return undefined;

    (window as any).__ygfE2E = {
      uploadLocalVideo: async (targetPath: string) => {
        const payload = await bridge.readFileForE2E(targetPath);
        const bytes = payload?.bytes instanceof Uint8Array ? payload.bytes : new Uint8Array(payload?.bytes || []);
        const file = new File([bytes], String(payload?.name || "smoke.mp4"), {
          type: String(payload?.mimeType || "video/mp4"),
        });
        Object.defineProperty(file, "path", {
          value: String(payload?.path || targetPath || ""),
          configurable: true,
        });
        await handleAddUpload({
          file,
          onSuccess: () => undefined,
          onError: (err: unknown) => {
            throw err;
          },
        } as any);
        return { ok: true, name: file.name };
      },
      getWizardState: () => ({
        taskCount: wizardTasks.length,
        taskNames: wizardTasks.map((item) => String(item.inputName || "")),
      }),
    };

    return () => {
      delete (window as any).__ygfE2E;
    };
  }, [handleAddUpload, wizardTasks]);

  function batchCounts(b: BatchModel) {
    const total = b.tasks.length;
    const done = b.tasks.filter((t) => t.state === "completed").length;
    const failed = b.tasks.filter((t) => t.state === "failed").length;
    const pending = b.tasks.filter((t) => t.state === "pending").length;
    const running = b.tasks.filter((t) => t.state === "running").length;
    const paused = b.tasks.filter((t) => t.state === "paused").length;
    const cancelled = b.tasks.filter((t) => t.state === "cancelled").length;
    return { total, done, failed, pending, running, paused, cancelled };
  }

  const { cancelCurrent, cancelCurrentById, resumeTaskInPlace, runReviewAndPoll, applyReviewAndRefresh } = useTaskActions({
    activeBatch,
    batchesRef,
    updateActiveBatch,
    startPollingForTask,
    deliverTaskToOutputDir,
    getTaskCreationBlockReason: () => taskCreationBlockReason,
  });

  function updateActiveBatchById(batchId: string, updater: (b: BatchModel) => BatchModel) {
    setBatches((prev) => prev.map((b) => (b.id === batchId ? updater(b) : b)));
  }

  const cancelTaskInBatch = useCallback(
    async (batchId: string, taskIdx: number) => {
      const b = batchesRef.current.find((x) => x.id === batchId);
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t?.taskId) return;
      try {
        await cancelTask(t.taskId);
        updateActiveBatchById(batchId, (bb) => {
          const tasks = [...bb.tasks];
          const prev = tasks[taskIdx];
          tasks[taskIdx] = {
            ...prev,
            state: "cancelled",
            message: "Cancelled",
            endedAt: Date.now() / 1000,
          };
          // If we cancelled the current task, clear pointer to avoid "stuck current".
          const nextCurrent = bb.currentTaskIndex === taskIdx ? undefined : bb.currentTaskIndex;
          return { ...bb, tasks, currentTaskIndex: nextCurrent };
        });
        message.success("已终止该任务");
      } catch (err: any) {
        message.error(err?.message || "终止失败");
      }
    },
    [batchesRef, updateActiveBatchById],
  );

  const restartTaskInBatch = useCallback(
    async (batchId: string, taskIdx: number) => {
      if (!ensureTaskCreationAllowed()) return;
      const b = batchesRef.current.find((x) => x.id === batchId);
      if (!b) return;
      const t = b.tasks[taskIdx];
      if (!t?.inputPath) return;
      try {
        const mergedParams = { ...(b.params || {}), ...((t as any).paramsOverride || {}) };
        const tid = await startTask({ video: t.inputPath, params: mergedParams, preset: b.preset, mode: b.mode });
        updateActiveBatchById(batchId, (bb) => {
          const tasks = [...bb.tasks];
          const prev = tasks[taskIdx];
          tasks[taskIdx] = {
            ...prev,
            taskId: tid,
            state: "running",
            progress: 0,
            stage: 0,
            stageName: "排队中",
            message: "Queued (waiting for available worker)",
            startedAt: Date.now() / 1000,
            endedAt: null,
            resumeFrom: null,
            createdAtBackend: null,
            resumedAt: null,
            failureReason: "",
            artifacts: [],
            qualityPassed: undefined,
            qualityErrors: undefined,
            qualityWarnings: undefined,
            deliveredDir: undefined,
            deliveredFiles: undefined,
          };
          return { ...bb, tasks, state: "running", currentTaskIndex: taskIdx };
        });
        // Start polling for the new backend task id
        startPollingForTask(batchId, taskIdx, tid);
        message.success("已重新开始");
      } catch (err: any) {
        message.error(err?.message || "重新开始失败");
      }
    },
    [batchesRef, startPollingForTask, taskCreationBlockReason, updateActiveBatchById],
  );

  const { finalizeTask } = useTaskFinalize({
    batchesRef,
    updateActiveBatchById,
    deliverTaskToOutputDir,
    updateBatchStateIfAllDone,
    startNextIfNeeded,
    tickGlobalQueue,
  });
  finalizeTaskRef.current = finalizeTask;

  const headerStatus = useMemo(() => {
    if (!activeBatch) return null;
    const counts = batchCounts(activeBatch);
    const runningIdx = activeBatch.tasks.findIndex((t) => t.state === "running");
    const current = runningIdx >= 0 ? activeBatch.tasks[runningIdx] : null;
    return { counts, current };
  }, [activeBatch]);

  // ---------------------------
  // Render: Wizard
  // ---------------------------
  const wizard = <WizardScreen />;

  // ---------------------------
  // Render: Workbench
  // ---------------------------
  const workbench = <WorkbenchScreen />;

  const history = <HistoryScreen />;

  const modeSelect = <ModeSelectScreen />;

  const advanced = <AdvancedScreen />;

  const system = <SystemScreen />;

  const providerValues = createAppProviderValues({
    wizardStep,
    setWizardStep,
    mode,
    wizardUploading,
    handleAddUpload,
    wizardTasks,
    removeTask,
    moveTask,
    setRoute,
    form,
    reviewEnabled,
    setReviewEnabled,
    batchName,
    setBatchName,
    outputDir,
    chooseOutputDir,
    openPath,
    subtitleSource,
    setSubtitleSource,
    regionPickerRect,
    setRegionRectSafe,
    regionPickerSampleFontSize,
    setFinalSubtitleFontSize,
    regionPickerFrameRef,
    regionPickerVideoPath,
    regionPickerVideoRef,
    setRegionPickerVideoReady,
    setRegionPickerVideoError,
    setRegionPickerVideoInfo,
    regionPickerVideoBox,
    regionPickerSampleText,
    regionPickerVideoScale,
    saveSubtitleSettings,
    hasUnsavedHasSettings,
    hasUnsavedNoneSettings,
    applySavedSubtitleSettings,
    createBatchAndGo,
    taskCreationBlocked,
    taskCreationBlockReason,
    canRenewInApp: cloudAuth.canRenewInApp,
    openRenewalModal,
    authUserEmail: cloudAuth.user?.email || "",
    authStatusText: cloudAuth.authStatusText,
    authLicenseExpireAt: cloudAuth.license?.expire_at || "",
    authLicenseLoading: cloudAuth.licenseInfoLoading,
    handleAuthLogout: cloudAuth.handleLogout,

    availableModes,
    config,
    uiPrefs,
    setMode,
    setUiPrefs,
    saveUiPrefs,

    batches,
    activeBatchId,
    batchCounts,
    resetWizard: openNewBatchWizard,
    setActiveBatchId,
    openTaskDrawer,
    openBatchOutputFolder,
    openDefaultOutputsFolder,
    deliverTaskToOutputDir,
    pauseQueueById,
    resumeQueueById,
    startQueue,
    safeStem,
    drawerOpen,
    drawerTaskIndex,
    drawerWidth,
    drawerInitialTab,
    drawerLog,
    drawerLogLoading,
    activeBatch,
    resumeTaskInPlace,
    runReviewAndPoll,
    applyReviewAndRefresh,
    exportDiagnosticZipForTask,
    openCleanupDialog,
    openQualityUpgradeWizardFromTask,
    onCloseDrawer: drawer.closeTaskDrawer,
    onGoSystem: () => setRoute("system"),
    cancelTaskInBatch,
    restartTaskInBatch,

    openDeliveredDirForTask,
    setBatches,
    activeBatchIdRef,
    rulesCenterModel,

    advancedShowAll,
    setAdvancedShowAll,
    toggleMeta,
    paramMeta,
    textMeta,
    stageOfToggle,
    riskTagColor,
    devToolsEnabled,

    apiBase,
    health,
    hardware,
    loadingBoot,
    bootstrap,
    requireLocalPacks,
    modelsReady,
    modelsRoot,
    modelsZipHint,
    missingLabels,
    modelsError,
    modelsImporting,
    handlePickModels,
    ollamaReady,
    ollamaPortOpen,
    ollamaRoot,
    ollamaModelsRoot,
    ollamaZipHint,
    ollamaProcessorSummary,
    ollamaAcceleration,
    ollamaActiveModels,
    ollamaUsesGpu,
    ollamaError,
    ollamaImporting,
    ollamaLoading,
    ollamaStarting,
    handlePickOllama,
    handleEnsureOllama,
    refreshOllamaStatus,
    runtimeStatus,
    runtimeLoading,
    runtimeError,
    refreshRuntimeStatus,
    preset,
    allowedPresetOptions,
    loadUiPrefs,
    setPreset,
  });

  const applyRegionPickerToForm = () => {
          const f = regionPickerTarget === "batch" ? form : overrideForm;
          const r = regionPickerRect;
          const round = (v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
          if (regionPickerPurpose === "subtitle") {
            f.setFieldsValue({
              sub_place_enable: true,
              sub_place_coord_mode: "ratio",
              sub_place_x: round(r.x),
              sub_place_y: round(r.y),
              sub_place_w: round(r.w),
              sub_place_h: round(r.h),
            });
            message.success("已应用字幕位置矩形（ratio）");
          } else {
            f.setFieldsValue({
              erase_subtitle_enable: true,
              erase_subtitle_coord_mode: "ratio",
              erase_subtitle_x: round(r.x),
              erase_subtitle_y: round(r.y),
              erase_subtitle_w: round(r.w),
              erase_subtitle_h: round(r.h),
              sub_place_enable: true,
              sub_place_coord_mode: "ratio",
              sub_place_x: round(r.x),
              sub_place_y: round(r.y),
              sub_place_w: round(r.w),
              sub_place_h: round(r.h),
            });
            message.success("已应用擦除区域（ratio），并同步字幕位置矩形");
          }
          setRegionPickerOpen(false);
  };

  return (
    <AppProviders values={providerValues}>
      <AppFrame
        route={route}
        setRoute={setRoute}
        mode={mode}
        siderCollapsed={siderCollapsed}
        setSiderCollapsed={setSiderCollapsed as any}
        screens={{ wizard, workbench, history, modeSelect, rulesCenter, advanced, system }}
        taskCreationBlocked={taskCreationBlocked}
        extras={
          <AppOverlays
            mode={mode}
            regionPickerFileInputRef={regionPickerFileInputRef as any}
            handleRegionPickerFileChange={handleRegionPickerFileChange}
            glossary={glossary}
            cleanupDialogOpen={cleanupDialogOpen}
            cleanupIncludeDiagnostics={cleanupIncludeDiagnostics}
            cleanupIncludeResume={cleanupIncludeResume}
            cleanupIncludeReview={cleanupIncludeReview}
            setCleanupIncludeDiagnostics={setCleanupIncludeDiagnostics}
            setCleanupIncludeResume={setCleanupIncludeResume}
            setCleanupIncludeReview={setCleanupIncludeReview}
            confirmCleanupArtifacts={confirmCleanupArtifacts as any}
            setCleanupDialogOpen={setCleanupDialogOpen}
            overrideModalOpen={overrideModalOpen}
            overrideEditing={overrideEditing}
            wizardTasks={wizardTasks}
            batchesRef={batchesRef as any}
            overrideForm={overrideForm as any}
            setOverrideModalOpen={setOverrideModalOpen}
            setOverrideEditing={setOverrideEditing}
            applyEraseSubOverrideToWizard={applyEraseSubOverrideToWizard}
            applyEraseSubOverrideToBatch={applyEraseSubOverrideToBatch}
            openRegionPickerFor={openRegionPickerFor as any}
            openRegionPicker={openRegionPicker as any}
            currentOverrideLocalPath={currentOverrideLocalPath}
            regionPickerPurpose={regionPickerPurpose}
            regionPickerOpen={regionPickerOpen}
            setRegionPickerOpen={setRegionPickerOpen}
            onApplyRegionPicker={applyRegionPickerToForm}
            regionPickerTarget={regionPickerTarget}
            regionPickerRect={regionPickerRect}
            setRegionRectSafe={setRegionRectSafe}
            regionPickerSampleFontSize={regionPickerSampleFontSize}
            setFinalSubtitleFontSize={setFinalSubtitleFontSize}
            regionPickerSampleText={regionPickerSampleText}
            setRegionPickerSampleText={setRegionPickerSampleText}
            regionPickerVideoPath={regionPickerVideoPath}
            regionPickerVideoReady={regionPickerVideoReady}
            regionPickerVideoError={regionPickerVideoError}
            regionPickerVideoInfo={regionPickerVideoInfo as any}
            regionPickerVideoRef={regionPickerVideoRef as any}
            setRegionPickerVideoReady={setRegionPickerVideoReady}
            setRegionPickerVideoError={setRegionPickerVideoError}
            setRegionPickerVideoInfo={setRegionPickerVideoInfo as any}
            regionPickerVideoBox={regionPickerVideoBox as any}
            regionPickerVideoScale={regionPickerVideoScale}
            resetRegionPickerVideo={resetRegionPickerVideo}
            showAuthGate={cloudAuth.showAuthGate}
            authStage={cloudAuth.authStage}
            authApiBase={cloudAuth.authApiBase}
            authEmail={cloudAuth.email}
            setAuthEmail={cloudAuth.setEmail}
            authCode={cloudAuth.code}
            setAuthCode={cloudAuth.setCode}
            authActivationCode={cloudAuth.activationCode}
            setAuthActivationCode={cloudAuth.setActivationCode}
            authStatusText={cloudAuth.authStatusText}
            authError={cloudAuth.authError}
            devCodeHint={cloudAuth.devCodeHint}
            authUserEmail={cloudAuth.user?.email || ""}
            authLicenseStatus={cloudAuth.license?.status || "none"}
            authLicenseExpireAt={cloudAuth.license?.expire_at || ""}
            authDeviceLimit={cloudAuth.deviceLimit}
            authActiveDeviceCount={cloudAuth.devices.length}
            authSendingCode={cloudAuth.sendingCode}
            authSendCodeCooldownSeconds={cloudAuth.sendCodeCooldownSeconds}
            authLoggingIn={cloudAuth.loggingIn}
            authRedeemingCode={cloudAuth.redeemingCode}
            authLicenseLoading={cloudAuth.licenseInfoLoading}
            handleAuthSendCode={cloudAuth.handleSendCode as any}
            handleAuthLogin={cloudAuth.handleLogin as any}
            handleAuthRedeemCode={handleRenewalRedeem as any}
            handleAuthLogout={cloudAuth.handleLogout as any}
            showRenewalModal={renewalModalOpen}
            canRenewInApp={cloudAuth.canRenewInApp}
            closeRenewalModal={() => setRenewalModalOpen(false)}
            requireLocalPacks={requireLocalPacks}
            modelsReady={modelsReady}
            modelsLoading={modelsLoading}
            modelsZipHint={modelsZipHint}
            missingLabels={missingLabels}
            modelsImporting={modelsImporting}
            handlePickModels={handlePickModels as any}
            modelsRoot={modelsRoot}
            modelsError={modelsError}
            ollamaReady={ollamaReady}
            ollamaPortOpen={ollamaPortOpen}
            ollamaZipHint={ollamaZipHint}
            ollamaImporting={ollamaImporting}
            ollamaStarting={ollamaStarting}
            handlePickOllama={handlePickOllama as any}
            handleEnsureOllama={handleEnsureOllama as any}
            ollamaRoot={ollamaRoot}
            ollamaError={ollamaError}
            ollamaLoading={ollamaLoading}
            refreshOllamaStatus={refreshOllamaStatus as any}
          />
        }
      />
    </AppProviders>
  );
};
export default App;

