import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
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
import {
  apiBase,
  cancelTask,
  cleanupTaskArtifacts,
  downloadTaskFileBytes,
  getArtifacts,
  getConfig,
  getHardware,
  getHealth,
  getLog,
  getGlossary,
  getEngSrt,
  getChsSrt2,
  putEngReviewSrt,
  putChsReviewSrt,
  applyReview,
  runReview,
  getQualityReport,
  getStatus,
  getTerminology,
  putGlossary,
  resumeTask2,
  startTask,
  uploadFile,
} from "./api";
import type { AppConfig, HardwareInfo, TaskStatus, Tier } from "./types";
import type { BatchModel, BatchTask, UiTaskState } from "./batchTypes";
import { loadActiveBatchId, loadBatches, loadUiPrefs, saveActiveBatchId, saveBatches, saveUiPrefs, type UiPrefs } from "./batchStorage";
import { defaultBatchName, nowTs, safeStem, twoDigitIndex } from "./utils";
import JSZip from "jszip";

const { Content, Sider } = Layout;
const { Title, Paragraph, Text } = Typography;
const { Option } = Select;

const DEFAULT_POLL_MS = 1200;

function uiStateFromBackend(state: TaskStatus["state"]): UiTaskState {
  if (state === "running") return "running";
  if (state === "completed") return "completed";
  if (state === "failed") return "failed";
  if (state === "cancelled") return "cancelled";
  if (state === "paused") return "paused";
  return "pending";
}

function tagColorForUiState(state: UiTaskState) {
  switch (state) {
    case "running":
      return "processing";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "paused":
      return "warning";
    case "cancelled":
      return "default";
    default:
      return "default";
  }
}

function shortReason(task: BatchTask): string {
  if (task.state === "failed") return task.failureReason || "失败（点开查看原因）";
  if (task.state === "paused") return "已暂停（需要你处理后继续）";
  if (task.state === "cancelled") return "已取消";
  if (task.state === "completed") {
    if (task.qualityPassed === false) return "已完成（质量检查未通过）";
    return "可交付";
  }
  if (task.state === "running") return task.stageName || task.message || "处理中…";
  return "等待处理";
}

function createId(): string {
  // @ts-ignore
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function demonstrateDownloadAnchor(a: HTMLAnchorElement, url: string, filename: string) {
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Allow the browser to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  demonstrateDownloadAnchor(a, url, filename);
}

function suggestForIssue(msg: string) {
  const s = (msg || "").toLowerCase();
  if (s.includes("missing") || s.includes("not found") || s.includes("缺失")) {
    return "建议：检查产物是否生成完整；必要时重新生成。";
  }
  if (s.includes("duration") || s.includes("length") || s.includes("时长")) {
    return "建议：检查视频/音频是否截断；可尝试重新生成。";
  }
  if (s.includes("srt") || s.includes("subtitle") || s.includes("字幕")) {
    return "建议：检查字幕格式与时间轴；必要时重新生成字幕。";
  }
  if (s.includes("audio") || s.includes("tts") || s.includes("音频")) {
    return "建议：检查配音质量；可尝试调整参数后重跑。";
  }
  return "建议：查看日志或导出诊断包定位原因。";
}

function issueTag(msg: string): { label: string; color: string } {
  const s = (msg || "").toLowerCase();
  if (s.includes("missing") || s.includes("not found") || s.includes("缺失")) return { label: "产物", color: "red" };
  if (s.includes("duration") || s.includes("length") || s.includes("时长") || s.includes("truncate")) return { label: "时长", color: "orange" };
  if (s.includes("srt") || s.includes("subtitle") || s.includes("字幕")) return { label: "字幕", color: "blue" };
  if (s.includes("audio") || s.includes("tts") || s.includes("音频")) return { label: "音频", color: "purple" };
  return { label: "其它", color: "default" };
}

function splitList(text: string): string[] {
  if (!text) return [];
  return text
    .split(/[,;\n]/g)
    .map((x) => x.trim())
    .filter(Boolean);
}

function joinList(items: string[]): string {
  return (items || []).filter(Boolean).join(", ");
}

const App: React.FC = () => {
  const [health, setHealth] = useState<string>("unknown");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);
  const [loadingBoot, setLoadingBoot] = useState(false);

  const [route, setRoute] = useState<"wizard" | "workbench" | "history" | "mode" | "advanced" | "system">("wizard");
  const [uiPrefs, setUiPrefs] = useState<UiPrefs>({});
  const [wizardStep, setWizardStep] = useState(0);
  const [subtitleSource, setSubtitleSource] = useState<"has" | "none">("has");
  const [reviewEnabled, setReviewEnabled] = useState(true);
  const [regionPickerPreviewSource, setRegionPickerPreviewSource] = useState("");
  const [glossaryModalOpen, setGlossaryModalOpen] = useState(false);
  const [glossaryItems, setGlossaryItems] = useState<
    { id: string; src: string; tgt: string; aliases: string; forbidden: string; note: string }[]
  >([]);
  const [glossaryLoading, setGlossaryLoading] = useState(false);
  const [glossaryError, setGlossaryError] = useState<string>("");
  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false);
  const [cleanupTaskIndex, setCleanupTaskIndex] = useState(-1);
  const [cleanupIncludeDiagnostics, setCleanupIncludeDiagnostics] = useState(true);
  const [cleanupIncludeResume, setCleanupIncludeResume] = useState(false);
  const [cleanupIncludeReview, setCleanupIncludeReview] = useState(false);
  const [savedSubtitleSettings, setSavedSubtitleSettings] = useState<{
    source: "has" | "none";
    values: Record<string, any>;
    rect?: { x: number; y: number; w: number; h: number };
    fontSize?: number;
  } | null>(null);
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [wizardTasks, setWizardTasks] = useState<
    { inputName: string; inputPath: string; localPath?: string; overrides?: Record<string, any> }[]
  >([]);
  const [wizardUploading, setWizardUploading] = useState(false);
  const [overrideModalOpen, setOverrideModalOpen] = useState(false);
  const [overrideEditing, setOverrideEditing] = useState<
    | { kind: "wizard"; wizardIdx: number }
    | { kind: "batch"; batchId: string; taskIndex: number }
    | null
  >(null);
  const [overrideForm] = Form.useForm();

  // Visual region picker (for erase_subtitle coords)
  const [regionPickerOpen, setRegionPickerOpen] = useState(false);
  const [regionPickerVideoPath, setRegionPickerVideoPath] = useState<string>("");
  const [regionPickerTarget, setRegionPickerTarget] = useState<"batch" | "override">("batch");
  const [regionPickerPurpose, setRegionPickerPurpose] = useState<"erase" | "subtitle">("erase");
  const [regionPickerRect, setRegionPickerRect] = useState<{ x: number; y: number; w: number; h: number }>({
    // Default: common burnt-subtitle area (bottom center-ish), fixed small height.
    x: 0.05,
    y: 0.72,
    w: 0.9,
    h: 0.1,
  });
  const [regionPickerVideoReady, setRegionPickerVideoReady] = useState(false);
  const [regionPickerVideoError, setRegionPickerVideoError] = useState<string>("");
  const [regionPickerVideoInfo, setRegionPickerVideoInfo] = useState<{ name?: string; duration?: number; w?: number; h?: number }>({});
  const [regionPickerSampleFontSize, setRegionPickerSampleFontSize] = useState<number>(18);
  const [regionPickerSampleText, setRegionPickerSampleText] = useState<string>("字幕的大小会是这样的");
  const regionPickerVideoRef = useRef<HTMLVideoElement | null>(null);
  const regionPickerFrameRef = useRef<HTMLDivElement | null>(null);
  const regionPickerFileInputRef = useRef<HTMLInputElement | null>(null);
  const baselineHasSettingsRef = useRef<ReturnType<typeof currentHasSettingsSnapshot> | null>(null);
  // Preview scale: map ASS/视频原始字号到当前预览窗口的 CSS 像素，保证“设置页预览 ≈ 成片效果（在同等缩放下）”
  const [regionPickerVideoScale, setRegionPickerVideoScale] = useState<number>(1);
  const [regionPickerVideoBox, setRegionPickerVideoBox] = useState<{ w: number; h: number; x: number; y: number }>({
    w: 0,
    h: 0,
    x: 0,
    y: 0,
  });

  const regionPickerActive = regionPickerOpen || (route === "wizard" && wizardStep === 1 && subtitleSource === "has");

  useEffect(() => {
    if (!regionPickerActive) return;
    const v = regionPickerVideoRef.current;
    if (!v) return;

    const update = () => {
      const vw = v.videoWidth || regionPickerVideoInfo.w || 0;
      const vh = v.videoHeight || regionPickerVideoInfo.h || 0;
      const cw = v.clientWidth || regionPickerFrameRef.current?.clientWidth || 0;
      const ch = v.clientHeight || regionPickerFrameRef.current?.clientHeight || 0;
      if (vw > 0 && vh > 0 && cw > 0 && ch > 0) {
        const aspect = vw / vh;
        const dispW = Math.min(cw, ch * aspect);
        const dispH = Math.min(ch, cw / aspect);
        const offsetX = (cw - dispW) / 2;
        const offsetY = (ch - dispH) / 2;
        const scale = dispW / vw;
        setRegionPickerVideoScale(scale > 0 && Number.isFinite(scale) ? scale : 1);
        setRegionPickerVideoBox({
          w: dispW,
          h: dispH,
          x: offsetX,
          y: offsetY,
        });
        return;
      }
      if (cw > 0 && ch > 0) {
        setRegionPickerVideoScale(1);
        setRegionPickerVideoBox({
          w: cw,
          h: ch,
          x: 0,
          y: 0,
        });
      }
    };

    update();
    let ro: ResizeObserver | null = null;
    try {
      ro = new ResizeObserver(update);
      ro.observe(v);
    } catch {
      ro = null;
    }
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("resize", update);
      try {
        ro?.disconnect();
      } catch {
        // ignore
      }
    };
  }, [regionPickerActive, regionPickerVideoPath, regionPickerVideoReady, regionPickerVideoInfo.w, regionPickerVideoInfo.h]);

  useEffect(() => {
    if (!(route === "wizard" && wizardStep === 1 && subtitleSource === "has")) return;
    if (regionPickerPurpose !== "erase") setRegionPickerPurpose("erase");
    if (regionPickerTarget !== "batch") setRegionPickerTarget("batch");
  }, [route, wizardStep, subtitleSource, regionPickerPurpose, regionPickerTarget]);

  function handleRegionPickerFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const url = URL.createObjectURL(f);
      setRegionPickerVideoPath(url);
      setRegionPickerVideoReady(false);
      setRegionPickerVideoError("");
      setRegionPickerVideoInfo({ name: f.name });
      message.success(`已选择预览视频：${f.name}`);
    } catch (err: any) {
      message.error(err?.message || "选择预览视频失败");
    } finally {
      e.target.value = "";
    }
  }

  function resetRegionPickerVideo() {
    setRegionPickerVideoPath("");
    setRegionPickerVideoReady(false);
    setRegionPickerVideoError("");
    setRegionPickerVideoInfo({});
  }

  const [form] = Form.useForm();
  const round3 = (v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
  function currentHasSettingsSnapshot() {
    const vals = form.getFieldsValue(true) || {};
    const rect = {
      x: round3(regionPickerRect.x),
      y: round3(regionPickerRect.y),
      w: round3(regionPickerRect.w),
      h: round3(regionPickerRect.h),
    };
    return {
      values: {
        erase_subtitle_x: round3(Number(vals.erase_subtitle_x || 0)),
        erase_subtitle_y: round3(Number(vals.erase_subtitle_y || 0)),
        erase_subtitle_w: round3(Number(vals.erase_subtitle_w || 0)),
        erase_subtitle_h: round3(Number(vals.erase_subtitle_h || 0)),
        erase_subtitle_blur_radius: Number(vals.erase_subtitle_blur_radius || 0),
      },
      rect,
      fontSize: Math.round((Number(regionPickerSampleFontSize) || 0) * 100) / 100,
    };
  }
  function hasUnsavedHasSettings() {
    if (subtitleSource !== "has") return false;
    if (!savedSubtitleSettings || savedSubtitleSettings.source !== "has") {
      const current = currentHasSettingsSnapshot();
      if (!baselineHasSettingsRef.current) {
        baselineHasSettingsRef.current = current;
        return false;
      }
      return JSON.stringify(current) !== JSON.stringify(baselineHasSettingsRef.current);
    }
    const current = currentHasSettingsSnapshot();
    const savedRect = savedSubtitleSettings.rect || { x: 0, y: 0, w: 0, h: 0 };
    const saved = {
      values: {
        erase_subtitle_x: round3(Number(savedSubtitleSettings.values?.erase_subtitle_x || 0)),
        erase_subtitle_y: round3(Number(savedSubtitleSettings.values?.erase_subtitle_y || 0)),
        erase_subtitle_w: round3(Number(savedSubtitleSettings.values?.erase_subtitle_w || 0)),
        erase_subtitle_h: round3(Number(savedSubtitleSettings.values?.erase_subtitle_h || 0)),
        erase_subtitle_blur_radius: Number(savedSubtitleSettings.values?.erase_subtitle_blur_radius || 0),
      },
      rect: {
        x: round3(Number(savedRect.x || 0)),
        y: round3(Number(savedRect.y || 0)),
        w: round3(Number(savedRect.w || 0)),
        h: round3(Number(savedRect.h || 0)),
      },
      fontSize: Math.round((Number(savedSubtitleSettings.fontSize) || 0) * 100) / 100,
    };
    return JSON.stringify(current) !== JSON.stringify(saved);
  }
  function saveSubtitleSettings() {
    if (subtitleSource === "has") {
      const vals = form.getFieldsValue(true) || {};
      setSavedSubtitleSettings({
        source: "has",
        values: {
          erase_subtitle_enable: true,
          erase_subtitle_coord_mode: "ratio",
          erase_subtitle_x: vals.erase_subtitle_x,
          erase_subtitle_y: vals.erase_subtitle_y,
          erase_subtitle_w: vals.erase_subtitle_w,
          erase_subtitle_h: vals.erase_subtitle_h,
          erase_subtitle_blur_radius: vals.erase_subtitle_blur_radius,
        },
        rect: { ...regionPickerRect },
        fontSize: regionPickerSampleFontSize,
      });
      message.success("已保存有字幕设置");
      return;
    }
    const vals = form.getFieldsValue(true) || {};
    setSavedSubtitleSettings({
      source: "none",
      values: {
        sub_font_size: vals.sub_font_size,
        sub_margin_v: vals.sub_margin_v,
        sub_outline: vals.sub_outline,
        sub_alignment: vals.sub_alignment,
      },
    });
    message.success("已保存无字幕设置");
  }

  function applySavedSubtitleSettings() {
    if (!savedSubtitleSettings) {
      setSubtitleSource("none");
      form.setFieldsValue({ erase_subtitle_enable: false });
      return;
    }
    setSubtitleSource(savedSubtitleSettings.source);
    form.setFieldsValue(savedSubtitleSettings.values || {});
    if (savedSubtitleSettings.rect) setRegionPickerRect(savedSubtitleSettings.rect);
    if (typeof savedSubtitleSettings.fontSize === "number") {
      setRegionPickerSampleFontSize(savedSubtitleSettings.fontSize);
      setFinalSubtitleFontSize(savedSubtitleSettings.fontSize);
    }
  }
  useEffect(() => {
    if (subtitleSource !== "has") {
      baselineHasSettingsRef.current = null;
      return;
    }
    if (savedSubtitleSettings && savedSubtitleSettings.source === "has") {
      baselineHasSettingsRef.current = null;
    }
  }, [subtitleSource, savedSubtitleSettings]);
  useEffect(() => {
    if (!(route === "wizard" && wizardStep === 1 && subtitleSource === "has")) return;
    const localPath = wizardTasks?.[0]?.localPath || "";
    if (!localPath) {
      if (regionPickerPreviewSource) {
        resetRegionPickerVideo();
        setRegionPickerPreviewSource("");
      }
      return;
    }
    if (regionPickerPreviewSource === localPath && regionPickerVideoPath) return;
    const src = toFileUrl(localPath);
    if (!src) return;
    setRegionPickerPreviewSource(localPath);
    setRegionPickerVideoPath(src);
    setRegionPickerVideoReady(false);
    setRegionPickerVideoError("");
    setRegionPickerVideoInfo({ name: wizardTasks?.[0]?.inputName || "预览视频" });
  }, [route, wizardStep, subtitleSource, regionPickerVideoPath, wizardTasks, regionPickerPreviewSource]);
  useEffect(() => {
    if (!(route === "wizard" && wizardStep === 1 && subtitleSource === "has")) return;
    const r = regionPickerRect;
    const round = (v: number) => Math.max(0, Math.min(1, Math.round(v * 1000) / 1000));
    form.setFieldsValue({
      erase_subtitle_enable: true,
      erase_subtitle_coord_mode: "ratio",
      erase_subtitle_x: round(r.x),
      erase_subtitle_y: round(r.y),
      erase_subtitle_w: round(r.w),
      erase_subtitle_h: round(r.h),
    });
  }, [route, wizardStep, subtitleSource, regionPickerRect, form]);
  const [mode, setMode] = useState<BatchModel["mode"]>("lite");
  const [availableModes, setAvailableModes] = useState<string[]>(["lite"]);
  const [preset, setPreset] = useState<string>("normal");
  const [batchName, setBatchName] = useState<string>(defaultBatchName());
  const [outputDir, setOutputDir] = useState<string>("");

  const [batches, setBatches] = useState<BatchModel[]>([]);
  const [activeBatchId, setActiveBatchId] = useState<string>("");
  const activeBatch = useMemo(() => batches.find((b) => b.id === activeBatchId) || null, [batches, activeBatchId]);
  const batchesRef = useRef<BatchModel[]>([]);
  const activeBatchIdRef = useRef<string>("");

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTaskIndex, setDrawerTaskIndex] = useState<number>(-1);
  const [drawerInitialTab, setDrawerInitialTab] = useState<string>("quality");
  const [drawerLog, setDrawerLog] = useState<string>("");
  const [drawerLogOffset, setDrawerLogOffset] = useState(0);
  const [drawerLogLoading, setDrawerLogLoading] = useState(false);
  const [advancedShowAll, setAdvancedShowAll] = useState(false);
  const [mtTopicModalOpen, setMtTopicModalOpen] = useState(false);
  const [mtTopicDraft, setMtTopicDraft] = useState("");

  const pollingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeBackendTaskIdRef = useRef<string>("");
  const pollingMs = useMemo(() => config?.ui?.polling_ms || DEFAULT_POLL_MS, [config]);

  const isBuildDev = !!import.meta.env.DEV;
  const devToolsEnabled = uiPrefs.devToolsEnabled ?? isBuildDev;

  useEffect(() => {
    bootstrap();
    const saved = loadBatches();
    setBatches(saved);
    const act = loadActiveBatchId();
    if (act && saved.some((b) => b.id === act)) {
      setActiveBatchId(act);
      setRoute("workbench");
    }
    return () => {
      if (pollingTimer.current) clearTimeout(pollingTimer.current);
      activeBackendTaskIdRef.current = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    saveBatches(batches);
    batchesRef.current = batches;
  }, [batches]);

  useEffect(() => {
    saveActiveBatchId(activeBatchId);
    activeBatchIdRef.current = activeBatchId;
  }, [activeBatchId]);

  async function bootstrap() {
    setLoadingBoot(true);
    try {
      const prefs = loadUiPrefs();
      setUiPrefs(prefs);
      const [cfg, hw, h] = await Promise.all([getConfig(), getHardware(), getHealth()]);
      setConfig(cfg);
      setHardware(hw);
      setHealth(h);

      const modes = cfg.available_modes && cfg.available_modes.length > 0 ? cfg.available_modes : ["lite"];
      setAvailableModes(modes);
      const serverDefaultMode = (cfg as any).default_mode || (cfg.defaults as any)?.default_mode;
      const pickedMode =
        prefs.defaultMode && modes.includes(prefs.defaultMode)
          ? prefs.defaultMode
          : modes.includes(serverDefaultMode as any)
            ? serverDefaultMode
            : (modes[0] || "lite");
      setMode(pickedMode as any);

      // recommended preset from hardware tier
      const tier = (hw.tier as Tier) || "normal";
      const presetGuess = cfg.presets?.[tier] ? tier : "normal";
      const pickedPreset = prefs.defaultPreset && cfg.presets?.[prefs.defaultPreset] ? prefs.defaultPreset : presetGuess;
      setPreset(pickedPreset);

      const merged = { ...(cfg.defaults || {}), ...(cfg.presets?.[pickedPreset] || {}) };
      const toggles = prefs.defaultToggles || {};
      const params = prefs.defaultParams || {};
      form.setFieldsValue(merged);
      // Apply preferred toggles (if present)
      for (const [k, v] of Object.entries(toggles)) {
        if (typeof v === "boolean") {
          form.setFieldsValue({ [k]: v });
        }
      }
      // Apply preferred scalar params (if present)
      for (const [k, v] of Object.entries(params)) {
        if (typeof v === "number" || typeof v === "string") {
          form.setFieldsValue({ [k]: v });
        }
      }
    } catch (err: any) {
      message.error(err?.message || "初始化失败");
    } finally {
      setLoadingBoot(false);
    }
  }

  function updateActiveBatch(updater: (b: BatchModel) => BatchModel) {
    setBatches((prev) => prev.map((b) => (b.id === activeBatchId ? updater(b) : b)));
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

  async function openDefaultOutputsFolder(relDir?: string) {
    const base = await getDefaultOutputsRoot();
    if (!base) {
      message.info("当前环境无法定位默认 outputs 目录（仅 Electron 桌面版支持）。");
      return;
    }
    const p = relDir ? `${base}/${relDir}` : base;
    await openPath(p);
  }

  function toFileUrl(p: string): string {
    const s = String(p || "");
    if (!s) return "";
    if (s.startsWith("http://") || s.startsWith("https://") || s.startsWith("file://")) return s;
    // Electron dev: file:// is blocked under http origin; use custom localfile:// protocol.
    // Expect absolute paths like /Users/...
    return `localfile://${encodeURI(s)}`;
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
    const y = Number(vals.erase_subtitle_y ?? 0.72);
    const w = w0;
    const h = Number(vals.erase_subtitle_h ?? 0.1);
    setRegionPickerRect({
      x: Number.isFinite(x) ? x : 0.0,
      y: Number.isFinite(y) ? y : 0.78,
      w: Number.isFinite(w) ? w : 1.0,
      h: Number.isFinite(h) ? h : 0.22,
    });
    setRegionPickerTarget(target);
    setRegionPickerPurpose(purpose);
    setRegionPickerVideoPath(src || "");
    setRegionPickerVideoReady(false);
    setRegionPickerVideoError("");
    setRegionPickerVideoInfo({});
    {
      const fs = Number(vals?.sub_font_size ?? 18);
      setRegionPickerSampleFontSize(Number.isFinite(fs) ? Math.max(10, Math.min(60, fs)) : 18);
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

  function clamp01(v: number): number {
    if (!Number.isFinite(v)) return 0;
    return Math.max(0, Math.min(1, v));
  }

  function setRegionRectSafe(patch: Partial<{ x: number; y: number; w: number; h: number }>) {
    setRegionPickerRect((prev) => {
      const next = { ...prev, ...patch };
      let x = clamp01(Number(next.x));
      let y = clamp01(Number(next.y));
      let w = clamp01(Number(next.w));
      let h = clamp01(Number(next.h));
      // Avoid 0-size rectangles
      w = Math.max(0.01, w);
      h = Math.max(0.01, h);

      // When changing width, keep the horizontal center (shrink/expand from both sides).
      if (patch.w !== undefined && Number.isFinite(prev.x) && Number.isFinite(prev.w)) {
        const centerX = clamp01(prev.x + prev.w / 2);
        x = centerX - w / 2;
      }
      // When changing height, keep the vertical center (shrink/expand from both sides).
      if (patch.h !== undefined && Number.isFinite(prev.y) && Number.isFinite(prev.h)) {
        const centerY = clamp01(prev.y + prev.h / 2);
        y = centerY - h / 2;
      }

      // Keep inside frame
      if (x < 0) x = 0;
      if (y < 0) y = 0;
      if (x + w > 1) x = Math.max(0, 1 - w);
      if (y + h > 1) y = Math.max(0, 1 - h);
      return { x, y, w, h };
    });
  }

  function currentOverrideLocalPath(): string {
    if (!overrideEditing) return "";
    if (overrideEditing.kind === "wizard") return wizardTasks?.[overrideEditing.wizardIdx]?.localPath || "";
    const b = batchesRef.current.find((x) => x.id === overrideEditing.batchId);
    return (b?.tasks?.[overrideEditing.taskIndex] as any)?.localPath || "";
  }

  function textToBytes(s: string): Uint8Array {
    return new TextEncoder().encode(s);
  }

  async function exportDiagnosticZipForTask(taskIdx: number, opts?: { includeMedia?: boolean }) {
    const b = activeBatch;
    if (!b) return;
    const t = b.tasks[taskIdx];
    if (!t.taskId) {
      message.error("该任务还没有 task_id");
      return;
    }
    if (!b.outputDir || !window.bridge?.writeFile || !window.bridge?.ensureDir) {
      message.info("请先选择输出文件夹（桌面版支持导出诊断包）。");
      return;
    }
    const includeMedia = !!opts?.includeMedia;
    try {
      message.loading({ content: "正在打包诊断包…", key: `zip_${t.taskId}`, duration: 0 });
      const zip = new JSZip();
      const baseName = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
      const relDir = baseName;
      const baseDir = b.outputDir || (await getDefaultOutputsRoot());
      if (!baseDir) {
        message.info("当前环境无法定位输出目录（仅 Electron 桌面版支持）。");
        return;
      }
      await window.bridge.ensureDir(baseDir, relDir);

      // 1) metadata
      zip.file("批次信息.json", JSON.stringify({ id: b.id, name: b.name, mode: b.mode, preset: b.preset, createdAt: b.createdAt }, null, 2));
      zip.file("任务信息.json", JSON.stringify({ index: t.index, inputName: t.inputName, taskId: t.taskId, state: t.state }, null, 2));

      // 2) quality report (prefer API; fallback to artifacts download)
      try {
        const qr = await getQualityReport(t.taskId);
        zip.file("质量摘要.json", JSON.stringify(qr, null, 2));
      } catch {
        const q = t.artifacts?.find((x) => x.name === "quality_report.json");
        if (q) {
          const bytes = await downloadTaskFileBytes(t.taskId, q.path);
          zip.file("质量摘要.json", bytes);
        }
      }

      // 3) log (via API, loop to full)
      let offset = 0;
      let logAll = "";
      for (let i = 0; i < 300; i++) {
        const lr = await getLog(t.taskId, offset);
        if (!lr?.content) break;
        logAll += lr.content;
        offset = lr.next_offset || (offset + lr.content.length);
        if (logAll.length > 2_000_000) break; // cap at ~2MB
      }
      zip.file("日志.txt", logAll || "暂无日志");

      // 4) key artifacts
      const wanted = new Set([
        "chs.srt",
        "eng.srt",
        "bilingual.srt",
        "chs.review.srt",
        "eng.review.srt",
        "terminology.json",
        "task_meta.json",
      ]);
      const media = new Set(["output_en_sub.mp4", "output_en.mp4", "tts_full.wav"]);
      for (const a of t.artifacts || []) {
        if (wanted.has(a.name) || (includeMedia && media.has(a.name))) {
          const bytes = await downloadTaskFileBytes(t.taskId, a.path);
          zip.file(a.name, bytes);
        }
      }

      const zipBytes = await zip.generateAsync({ type: "uint8array" });
      const zipName = includeMedia ? "诊断包_含媒体.zip" : "诊断包.zip";
      await window.bridge.writeFile(baseDir, `${relDir}/${zipName}`, zipBytes);
      message.success({ content: "诊断包已导出", key: `zip_${t.taskId}` });
      await openPath(`${baseDir}/${relDir}`);
    } catch (err: any) {
      message.error({ content: err?.message || "导出诊断包失败", key: `zip_${t.taskId}` });
  }
  }

  function rowsFromGlossary(doc: any) {
    const items = Array.isArray(doc?.items) ? doc.items : [];
    return items.map((it: any, idx: number) => ({
      id: String(it?.id || `t${String(idx + 1).padStart(4, "0")}`),
      src: String(it?.src || ""),
      tgt: String(it?.tgt || ""),
      aliases: joinList(Array.isArray(it?.aliases) ? it.aliases : []),
      forbidden: joinList(Array.isArray(it?.forbidden) ? it.forbidden : []),
      note: String(it?.note || ""),
    }));
  }

  function glossaryDocFromRows(rows: { id: string; src: string; tgt: string; aliases: string; forbidden: string; note: string }[]) {
    return {
      version: 1,
      items: rows
        .filter((r) => r.src && r.src.trim())
        .map((r, idx) => ({
          id: r.id || `t${String(idx + 1).padStart(4, "0")}`,
          src: r.src.trim(),
          tgt: (r.tgt || "").trim(),
          aliases: splitList(r.aliases || ""),
          forbidden: splitList(r.forbidden || ""),
          note: (r.note || "").trim(),
          scope: "global",
        })),
    };
  }

  async function openGlossaryModal() {
    setGlossaryModalOpen(true);
    setGlossaryLoading(true);
    setGlossaryError("");
    try {
      const res = await getGlossary();
      setGlossaryItems(rowsFromGlossary(res));
    } catch (err: any) {
      setGlossaryItems([]);
      setGlossaryError(err?.message || "加载失败");
    } finally {
      setGlossaryLoading(false);
    }
  }

  async function saveGlossary() {
    setGlossaryLoading(true);
    setGlossaryError("");
    try {
      const doc = glossaryDocFromRows(glossaryItems);
      await putGlossary(doc);
      message.success("已保存术语表");
    } catch (err: any) {
      message.error(err?.message || "保存失败");
    } finally {
      setGlossaryLoading(false);
    }
  }

  function updateGlossaryRow(id: string, patch: Partial<{ src: string; tgt: string; aliases: string; forbidden: string; note: string }>) {
    setGlossaryItems((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function addGlossaryRow() {
    setGlossaryItems((prev) => [
      ...prev,
      { id: createId(), src: "", tgt: "", aliases: "", forbidden: "", note: "" },
    ]);
  }

  function removeGlossaryRow(id: string) {
    setGlossaryItems((prev) => prev.filter((r) => r.id !== id));
  }

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
    resetRegionPickerVideo();
    setDrawerOpen(false);
    setDrawerTaskIndex(-1);
    setRoute("wizard");
  }

  function presetLabel(key: string): string {
    if (key === "high") return "更清晰（高端）";
    if (key === "mid") return "更快（中端）";
    if (key === "normal") return "更省资源（普通）";
    return key;
  }

  function modeLabel(m: BatchModel["mode"]): string {
    if (m === "lite") return "轻量";
    if (m === "quality") return "质量";
    if (m === "online") return "在线";
    return String(m);
  }

  function batchStateLabel(s: BatchModel["state"]): string {
    if (s === "running") return "进行中";
    if (s === "queued") return "排队中";
    if (s === "paused") return "已暂停";
    if (s === "completed") return "已结束";
    if (s === "draft") return "未开始";
    return String(s);
  }

  function taskStateLabel(s: UiTaskState): string {
    if (s === "pending") return "待处理";
    if (s === "running") return "处理中";
    if (s === "completed") return "已完成";
    if (s === "failed") return "失败";
    if (s === "paused") return "已暂停";
    if (s === "cancelled") return "已取消";
    return String(s);
  }

  function batchOutputRoot(b: BatchModel): string {
    if (!b.outputDir) return "";
    return b.outputDir;
  }

  async function getDefaultOutputsRoot(): Promise<string> {
    const projectRoot = (await window.bridge?.getProjectRoot?.()) || "";
    const base = projectRoot || (await window.bridge?.getCwd?.()) || "";
    return base ? `${base}/outputs` : "";
  }

  async function openBatchOutputFolder(b: BatchModel) {
    try {
      const base = b.outputDir || (await getDefaultOutputsRoot());
      if (!base) {
        message.info("当前环境无法定位输出目录。");
        return;
      }
      const delivered = (b.tasks || []).filter((t) => !!t.deliveredDir);
      const target =
        delivered.length === 1 ? `${base}/${delivered[0].deliveredDir}` : base;
      await openPath(target);
    } catch (err: any) {
      message.error(err?.message || "打开失败");
    }
  }

  async function openDeliveredDirForTask(b: BatchModel, t: BatchTask) {
    try {
      const base = b.outputDir || (await getDefaultOutputsRoot());
      if (!base) {
        message.info("当前环境无法定位输出目录。");
        return;
      }
      if (!t.deliveredDir) {
        message.info("该任务尚未交付");
        return;
      }
      await openPath(`${base}/${t.deliveredDir}`);
    } catch (err: any) {
      message.error(err?.message || "打开失败");
    }
  }

  const PER_TASK_OVERRIDE_KEYS = [
    "erase_subtitle_enable",
    "erase_subtitle_method",
    "erase_subtitle_coord_mode",
    "erase_subtitle_x",
    "erase_subtitle_y",
    "erase_subtitle_w",
    "erase_subtitle_h",
    "erase_subtitle_blur_radius",
    // subtitle burn-in style
    "sub_font_name",
    "sub_font_size",
    "sub_outline",
    "sub_shadow",
    "sub_margin_v",
    "sub_alignment",
    // subtitle placement box (optional)
    "sub_place_enable",
    "sub_place_coord_mode",
    "sub_place_x",
    "sub_place_y",
    "sub_place_w",
    "sub_place_h",
    // mux sync (hearing-first)
    "mux_sync_strategy",
    "mux_slow_max_ratio",
    "mux_slow_threshold_s",
  ] as const;

  function pickPerTaskOverrideValues(src: Record<string, any>): Record<string, any> {
    const out: Record<string, any> = {};
    for (const k of PER_TASK_OVERRIDE_KEYS) out[k] = src?.[k];
    return out;
  }

  function normalizePerTaskOverrideValues(vals: Record<string, any>): Record<string, any> {
    // 只保存这组字段，避免把其它批次参数误当成“单视频覆盖”
    const picked = pickPerTaskOverrideValues(vals || {});
    // 去掉 undefined，保持存储干净
    for (const [k, v] of Object.entries(picked)) {
      if (v === undefined) delete picked[k];
    }
    return picked;
  }

  function openEraseSubOverrideEditor(target: { kind: "wizard"; wizardIdx: number } | { kind: "batch"; batchId: string; taskIndex: number }) {
    setOverrideEditing(target as any);
    const batchBase = form.getFieldsValue(true) || {};
    let current: Record<string, any> = {};
    if (target.kind === "wizard") {
      current = wizardTasks[target.wizardIdx]?.overrides || {};
    } else {
      const b = batchesRef.current.find((x) => x.id === target.batchId);
      current = (b?.tasks?.[target.taskIndex] as any)?.paramsOverride || {};
    }
    const merged = { ...pickPerTaskOverrideValues(batchBase), ...pickPerTaskOverrideValues(current) };
    overrideForm.setFieldsValue(merged);
    setOverrideModalOpen(true);
  }

  function applyEraseSubOverrideToWizard(wizardIdx: number, values: Record<string, any>) {
    setWizardTasks((prev) => {
      const next = [...prev];
      const item = next[wizardIdx];
      next[wizardIdx] = { ...item, overrides: normalizePerTaskOverrideValues(values) };
      return next;
    });
  }

  function applyEraseSubOverrideToBatch(batchId: string, taskIndex: number, values: Record<string, any>) {
    setBatches((prev) =>
      prev.map((b) => {
        if (b.id !== batchId) return b;
        const tasks = [...b.tasks];
        const t = tasks[taskIndex];
        tasks[taskIndex] = { ...t, paramsOverride: normalizePerTaskOverrideValues(values) };
        return { ...b, tasks };
      }),
    );
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

  // Ensure preset matches current mode (avoid mixing lite presets into quality mode).
  useEffect(() => {
    const allow = new Set(allowedPresetKeysForMode(mode));
    if (allow.size === 0) return;
    if (!allow.has(preset)) {
      const next = Array.from(allow)[0];
      setPreset(next);
    }
  }, [mode, config]);

  async function handleAddUpload(options: RcCustomRequestOptions) {
    const file = options.file as File;
    try {
      setWizardUploading(true);
      const path = await uploadFile(file);
      const localPath = String((file as any)?.path || "");
      setWizardTasks((prev) => [...prev, { inputName: file.name, inputPath: path, localPath, overrides: {} }]);
      options.onSuccess?.({ path }, new XMLHttpRequest());
      message.success(`已添加：${file.name}`);
    } catch (err: any) {
      options.onError?.(err);
      message.error(err?.message || "上传失败");
    } finally {
      setWizardUploading(false);
    }
  }

  function moveTask(idx: number, delta: -1 | 1) {
    setWizardTasks((prev) => {
      const next = [...prev];
      const j = idx + delta;
      if (j < 0 || j >= next.length) return prev;
      const tmp = next[idx];
      next[idx] = next[j];
      next[j] = tmp;
      return next;
    });
  }

  function removeTask(idx: number) {
    setWizardTasks((prev) => prev.filter((_, i) => i !== idx));
  }

  async function createBatchAndGo(startNow: boolean) {
    if (wizardTasks.length === 0) {
      message.error("请先添加视频");
        return;
      }
    if (!outputDir) {
      // Allow without output dir (web mode), but warn.
      message.warning("你还没有选择输出文件夹。你仍可继续，但需要手动下载交付物。");
    }
    const params = form.getFieldsValue(true) || {};
    params.review_enabled = reviewEnabled;
    if (savedSubtitleSettings?.source === "has") {
      const rect = savedSubtitleSettings.rect || regionPickerRect;
      params.erase_subtitle_enable = true;
      params.erase_subtitle_coord_mode = "ratio";
      params.erase_subtitle_x = rect.x;
      params.erase_subtitle_y = rect.y;
      params.erase_subtitle_w = rect.w;
      params.erase_subtitle_h = rect.h;
      params.sub_place_enable = true;
      params.sub_place_coord_mode = "ratio";
      params.sub_place_x = rect.x;
      params.sub_place_y = rect.y;
      params.sub_place_w = rect.w;
      params.sub_place_h = rect.h;
      if (typeof savedSubtitleSettings.fontSize === "number") {
        params.sub_font_size = savedSubtitleSettings.fontSize;
      }
    } else if (savedSubtitleSettings?.source === "none") {
      params.erase_subtitle_enable = false;
      Object.assign(params, savedSubtitleSettings.values || {});
    } else {
      params.erase_subtitle_enable = false;
    }
    const batch: BatchModel = {
      id: createId(),
      name: batchName || defaultBatchName(),
      createdAt: nowTs(),
      mode,
      preset,
      params,
      outputDir: outputDir || "",
      state: startNow ? "running" : "draft",
      tasks: wizardTasks.map((t, i) => ({
        index: i + 1,
        inputName: t.inputName,
        inputPath: t.inputPath,
        localPath: (t as any)?.localPath || "",
        state: "pending",
        paramsOverride: t.overrides || {},
      })),
    };
    setBatches((prev) => [batch, ...prev]);
    setActiveBatchId(batch.id);
    if (startNow) {
      // UX：开始处理后，清空“新建批次”向导，方便继续加下一批
      setWizardStep(0);
      setWizardTasks([]);
      setBatchName(defaultBatchName());
      // 保留 outputDir / mode / preset / params（更贴近日常使用）
      Modal.confirm({
        title: "已开始处理",
        content: `批次「${batch.name}」已加入队列。你可以去「任务中心」查看进度，或继续新建下一批。`,
        centered: true,
        okText: "去任务中心",
        cancelText: "继续新建",
        onOk: () => setRoute("workbench"),
        onCancel: () => setRoute("wizard"),
      });
      setRoute("wizard");
      // start after state applied
      setTimeout(() => startQueue(batch.id, { navigate: false }), 0);
      return;
    }
    setRoute("workbench");
  }

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

  function stopPolling() {
    if (pollingTimer.current) {
      clearTimeout(pollingTimer.current);
      pollingTimer.current = null;
    }
    activeBackendTaskIdRef.current = "";
  }

  function batchHasUnfinishedTasks(b: BatchModel) {
    return b.tasks.some((t) => t.state === "pending" || t.state === "running");
  }

  function findBatchIdWithRunningTask(list: BatchModel[]): string {
    const hit = list.find((b) => b.tasks.some((t) => t.state === "running"));
    return hit?.id || "";
  }

  function findNextQueuedBatch(list: BatchModel[]): BatchModel | null {
    const queued = list
      .filter((b) => b.state === "queued" && batchHasUnfinishedTasks(b))
      .slice()
      .sort((a, c) => a.createdAt - c.createdAt);
    return queued[0] || null;
  }

  function tickGlobalQueue() {
    const list = batchesRef.current;
    // 如果有任何任务在跑，就不调度下一批
    if (findBatchIdWithRunningTask(list)) return;
    const next = findNextQueuedBatch(list);
    if (!next) return;
    setBatches((prev) => prev.map((b) => (b.id === next.id ? { ...b, state: "running" } : b)));
    setTimeout(() => startNextIfNeeded(next.id), 0);
  }

  async function startQueue(batchId: string, opts?: { navigate?: boolean }) {
    const navigate = opts?.navigate ?? true;
    setActiveBatchId(batchId);
    if (navigate) setRoute("workbench");

    // 如果该批次没有待处理任务，但存在“已取消”，则从第一个取消任务开始重置为待处理再继续
    setBatches((prev) =>
      prev.map((b) => {
        if (b.id !== batchId) return b;
        const hasUnfinished = b.tasks.some((t) => t.state === "pending" || t.state === "running");
        if (hasUnfinished) return b;
        const firstCancelled = b.tasks.findIndex((t) => t.state === "cancelled");
        if (firstCancelled < 0) return b;
        const tasks = b.tasks.map((t, i) => {
          if (i < firstCancelled) return t;
          if (t.state !== "cancelled") return t;
          return {
            ...t,
            state: "pending" as const,
            taskId: undefined,
            progress: 0,
            stageName: "",
            message: "",
            startedAt: undefined,
            endedAt: undefined,
            workDir: undefined,
            failureReason: "",
            artifacts: [],
            qualityPassed: undefined,
            qualityErrors: [],
            qualityWarnings: [],
          };
        });
        return { ...b, tasks, currentTaskIndex: undefined };
      }),
    );

    const list = batchesRef.current;
    const otherRunningTaskBatchId = list.find((b) => b.id !== batchId && b.tasks.some((t) => t.state === "running"))?.id || "";
    const otherRunningBatchId =
      list.find((b) => b.id !== batchId && b.state === "running" && batchHasUnfinishedTasks(b))?.id || "";
    const shouldQueue = !!(otherRunningTaskBatchId || otherRunningBatchId);

    setBatches((prev) =>
      prev.map((b) => (b.id === batchId ? { ...b, state: shouldQueue ? "queued" : "running" } : b)),
    );

    if (!shouldQueue) {
      setTimeout(() => startNextIfNeeded(batchId), 0);
    } else {
      message.info("已加入队列：当前有任务正在处理，会在前一批完成后自动开始。");
      setTimeout(() => tickGlobalQueue(), 0);
    }
  }

  async function pauseQueue() {
    if (!activeBatch) return;
    updateActiveBatch((b) => ({ ...b, state: "paused" }));
    message.info("已暂停队列：当前任务会继续运行，完成后不会自动进入下一个。");
  }

  async function resumeQueue() {
    if (!activeBatch) return;
    updateActiveBatch((b) => ({ ...b, state: "running" }));
    setTimeout(() => startNextIfNeeded(activeBatch.id), 0);
  }

  async function cancelCurrent() {
    const b = activeBatch;
    if (!b) return;
    const idx = b.currentTaskIndex ?? -1;
    if (idx < 0) return;
    const t = b.tasks[idx];
    if (!t.taskId || t.state !== "running") return;
    try {
      await cancelTask(t.taskId);
      message.success("已请求取消当前任务");
    } catch (err: any) {
      message.error(err?.message || "取消失败");
        }
  }

  async function pauseQueueById(batchId: string) {
    updateActiveBatchById(batchId, (b) => ({ ...b, state: "paused" }));
    message.info("已暂停队列：当前任务会继续运行，完成后不会自动进入下一个。");
  }

  async function resumeQueueById(batchId: string) {
    updateActiveBatchById(batchId, (b) => ({ ...b, state: "running" }));
    setTimeout(() => startNextIfNeeded(batchId), 0);
  }

  async function cancelCurrentById(batchId: string) {
    const b = batchesRef.current.find((x) => x.id === batchId);
    if (!b) return;
    const idx = b.currentTaskIndex ?? b.tasks.findIndex((t) => t.state === "running");
    if (idx < 0) return;
    const t = b.tasks[idx];
    if (!t.taskId || t.state !== "running") return;
    try {
      await cancelTask(t.taskId);
      message.success("已请求取消当前任务");
    } catch (err: any) {
      message.error(err?.message || "取消失败");
    }
  }

  async function resumeTaskInPlace(taskIdx: number, resumeFrom: "asr" | "mt" | "tts" | "mux") {
    const b = activeBatch;
    if (!b) return;
    const t = b.tasks[taskIdx];
    if (!t.taskId) {
      message.error("该任务还没有 task_id");
        return;
      }
    try {
      updateActiveBatch((bb) => {
        const next = { ...bb, tasks: [...bb.tasks] };
        next.tasks[taskIdx] = { ...next.tasks[taskIdx], state: "running", failureReason: "" };
        next.currentTaskIndex = taskIdx;
        return next;
      });
      const mergedParams = { ...(b.params || {}), ...((t as any).paramsOverride || {}) };
      const rid = await resumeTask2(t.taskId, { resume_from: resumeFrom, params: mergedParams, preset: b.preset });
      startPollingForTask(b.id, taskIdx, rid);
      message.success("已从上次继续");
    } catch (err: any) {
      message.error(err?.message || "继续失败");
    }
  }

  async function runReviewAndPoll(taskIdx: number, lang: "chs" | "eng") {
    const b = activeBatch;
    if (!b) return;
    const t = b.tasks[taskIdx];
    if (!t.taskId) {
      message.error("该任务还没有 task_id");
      return;
    }
    try {
      updateActiveBatch((bb) => ({ ...bb, state: "running", currentTaskIndex: taskIdx }));
      updateActiveBatch((bb) => {
        const tasks = [...bb.tasks];
        tasks[taskIdx] = { ...tasks[taskIdx], state: "running", message: "正在重新生成…" };
        return { ...bb, tasks };
      });
      const res = await runReview(t.taskId, lang);
      startPollingForTask(b.id, taskIdx, res.task_id);
      message.success("已开始重新生成（后台处理中）");
    } catch (err: any) {
      message.error(err?.message || "重新生成失败");
    }
  }

  async function applyReviewAndRefresh(taskIdx: number, action: "mux" | "embed" | "mux_embed", use: "review" | "base" = "review") {
    const b = activeBatch;
    if (!b) return;
    const t = b.tasks[taskIdx];
    if (!t.taskId) {
      message.error("该任务还没有 task_id");
      return;
    }
    try {
      message.loading({ content: "正在应用审校并生成交付物…", key: `apply_${t.taskId}`, duration: 0 });
      // Important: pass current effective params so regen respects latest UI settings (font size / placement box etc.)
      const effectiveParams = { ...(b.params || {}), ...((t as any).paramsOverride || {}) };
      await applyReview(t.taskId, { action, use, params: effectiveParams });
      // refresh artifacts + quality report (best-effort)
      const [arts, qr] = await Promise.all([
        getArtifacts(t.taskId).catch(() => []),
        getQualityReport(t.taskId).catch(() => null),
      ]);
      updateActiveBatch((bb) => {
        const tasks = [...bb.tasks];
        tasks[taskIdx] = {
          ...tasks[taskIdx],
          artifacts: arts,
          qualityPassed: qr ? !!qr.passed : tasks[taskIdx].qualityPassed,
          qualityErrors: qr?.errors || tasks[taskIdx].qualityErrors,
          qualityWarnings: qr?.warnings || tasks[taskIdx].qualityWarnings,
        };
        return { ...bb, tasks };
      });
      message.success({ content: "已应用审校（交付物已更新）", key: `apply_${t.taskId}` });
      // re-deliver to output dir if configured
      if (b.outputDir && window.bridge?.writeFile && window.bridge?.ensureDir) {
        await deliverTaskToOutputDir(b.id, taskIdx);
      }
    } catch (err: any) {
      message.error({ content: err?.message || "应用失败", key: `apply_${t.taskId}` });
    }
  }

  async function startNextIfNeeded(batchId: string) {
    // 全局串行：如果有其它批次正在跑任务，则本批次不启动新任务
    const runningTaskBatchId = findBatchIdWithRunningTask(batchesRef.current);
    if (runningTaskBatchId && runningTaskBatchId !== batchId) return;

    const b = batchesRef.current.find((x) => x.id === batchId);
    if (!b) return;
    if (b.state !== "running") return;
    // if already have a running task, keep polling
    const runningIdx = b.tasks.findIndex((t) => t.state === "running");
    if (runningIdx >= 0) {
      const taskId = b.tasks[runningIdx].taskId;
      if (taskId) startPollingForTask(batchId, runningIdx, taskId);
      return;
    }
    const nextIdx = b.tasks.findIndex((t) => t.state === "pending");
    if (nextIdx < 0) {
      // 如果没有 pending 但存在已取消，则从第一个取消任务开始重置为 pending 再继续
      const firstCancelled = b.tasks.findIndex((t) => t.state === "cancelled");
      if (firstCancelled >= 0) {
        updateActiveBatchById(batchId, (bb) => {
          const tasks = bb.tasks.map((t, i) => {
            if (i < firstCancelled) return t;
            if (t.state !== "cancelled") return t;
            return {
              ...t,
              state: "pending" as const,
              taskId: undefined,
              progress: 0,
              stageName: "",
              message: "",
              startedAt: undefined,
              endedAt: undefined,
              workDir: undefined,
              failureReason: "",
              artifacts: [],
              qualityPassed: undefined,
              qualityErrors: [],
              qualityWarnings: [],
            };
          });
          return { ...bb, tasks, currentTaskIndex: undefined };
        });
        setTimeout(() => startNextIfNeeded(batchId), 0);
        return;
      }
      // finished
      updateBatchStateIfAllDone(batchId);
      setTimeout(() => tickGlobalQueue(), 0);
      return;
    }
    try {
      const nextTask = b.tasks[nextIdx];
      updateBatchForTaskStart(batchId, nextIdx);
      const mergedParams = { ...(b.params || {}), ...((nextTask as any).paramsOverride || {}) };
      const id = await startTask({ video: nextTask.inputPath, params: mergedParams, preset: b.preset, mode: b.mode });
      updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        tasks[nextIdx] = { ...tasks[nextIdx], taskId: id, state: "running", startedAt: Date.now() };
        return { ...bb, tasks, currentTaskIndex: nextIdx };
      });
      startPollingForTask(batchId, nextIdx, id);
      message.success(`已开始：${nextTask.inputName}`);
    } catch (err: any) {
      // Mark as failed and continue
      updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        tasks[nextIdx] = { ...tasks[nextIdx], state: "failed", failureReason: err?.message || "启动失败" };
        return { ...bb, tasks };
      });
      message.error(err?.message || "启动失败");
      setTimeout(() => startNextIfNeeded(batchId), 0);
    }
  }

  function updateActiveBatchById(batchId: string, updater: (b: BatchModel) => BatchModel) {
    setBatches((prev) => prev.map((b) => (b.id === batchId ? updater(b) : b)));
  }

  function updateBatchForTaskStart(batchId: string, taskIdx: number) {
    updateActiveBatchById(batchId, (bb) => {
      const tasks = [...bb.tasks];
      tasks[taskIdx] = { ...tasks[taskIdx], state: "running", progress: 0, stageName: "", message: "开始处理中…" };
      return { ...bb, tasks, currentTaskIndex: taskIdx };
    });
  }

  function updateBatchStateIfAllDone(batchId: string) {
    updateActiveBatchById(batchId, (bb) => {
      const allFinished = bb.tasks.every((t) => ["completed", "failed", "cancelled", "paused"].includes(t.state));
      if (!allFinished) return bb;
      return { ...bb, state: "completed", currentTaskIndex: undefined };
    });
  }

  async function finalizeTask(batchId: string, taskIdx: number, taskId: string, st: TaskStatus) {
    try {
      const [arts, qr] = await Promise.all([
        getArtifacts(taskId).catch(() => []),
        getQualityReport(taskId, { regen: true }).catch(() => null),
      ]);
      updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        const prev = tasks[taskIdx];
        const failureReason =
          st.state === "failed"
            ? (st.message && !/^Exited with \d+$/i.test(st.message) ? st.message : qr?.errors?.[0] || "失败（点开查看原因）")
            : st.state === "paused"
              ? "已暂停：需要你处理后继续"
              : st.state === "cancelled"
                ? "已取消"
                : "";
        tasks[taskIdx] = {
          ...prev,
          state: uiStateFromBackend(st.state),
          progress: st.progress,
          stageName: st.stage_name,
          message: st.message,
          startedAt: st.started_at ? Math.floor(st.started_at * 1000) : prev.startedAt,
          endedAt: st.ended_at ? Math.floor(st.ended_at * 1000) : null,
          workDir: st.work_dir,
          artifacts: arts,
          qualityPassed: qr ? !!qr.passed : undefined,
          qualityErrors: qr?.errors || [],
          qualityWarnings: qr?.warnings || [],
          failureReason,
        };
        return { ...bb, tasks };
      });

      // auto-delivery (best-effort)
      // auto-delivery (best-effort): always use latest batch snapshot
      const latest = batchesRef.current.find((x) => x.id === batchId);
      if (window.bridge?.writeFile && window.bridge?.ensureDir) {
        try {
          // 注意：这里直接用本次拉到的 artifacts，避免 React state 未及时同步导致“没自动保存”
          await deliverTaskToOutputDir(batchId, taskIdx, arts);
        } catch (err: any) {
          message.warning({
            content: `自动保存交付物失败（可在“交付物”里手动下载）：${err?.message || "未知错误"}`,
            key: `deliver_auto_${taskId}`,
          });
        }
      }
    } catch {
      // swallow (but still allow queue to proceed)
    } finally {
      updateBatchStateIfAllDone(batchId);
      setTimeout(() => {
        startNextIfNeeded(batchId);
        tickGlobalQueue();
      }, 0);
    }
  }

  async function deliverTaskToOutputDir(
    batchId: string,
    taskIdx: number,
    artifactsOverride?: { name: string; path: string; size: number }[],
  ) {
    const b = batchesRef.current.find((x) => x.id === batchId);
    if (!b) return;
    const t = b.tasks[taskIdx];
    let arts = artifactsOverride || t.artifacts || [];
    if (!t.taskId) return;
    // 某些情况下，任务刚结束时 artifacts 还没刷新到前端 state；这里补一次拉取 + 重试，避免“没自动保存”
    if (!arts || arts.length === 0) {
      try {
        arts = await getArtifacts(t.taskId);
      } catch {
        arts = [];
      }
    }
    if (!arts || arts.length === 0) return;
    const baseDir = b.outputDir || (await getDefaultOutputsRoot());
    if (!baseDir) return;
    const ensureDir = window.bridge?.ensureDir;
    const writeFile = window.bridge?.writeFile;
    if (!ensureDir || !writeFile) return;

    const baseName = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
    const relDir = baseName;
    await ensureDir(baseDir, relDir);

    const wanted: { name: string; label: string; out: string }[] = [
      { name: "output_en_sub.mp4", label: "成片（带字幕）", out: `${baseName}.mp4` },
      { name: "chs.srt", label: "字幕（中文）", out: "chs.srt" },
      { name: "eng.srt", label: "字幕（英文）", out: "eng.srt" },
      { name: "bilingual.srt", label: "字幕（双语）", out: "bilingual.srt" },
    ];

    const present = wanted
      .map((w) => ({ w, a: arts.find((x) => x.name === w.name) }))
      .filter((x) => !!x.a) as { w: (typeof wanted)[number]; a: { name: string; path: string; size: number } }[];

    if (present.length === 0) return;

    // Small UX: show one message, keep it brief
    message.loading({ content: `正在保存交付物：${t.inputName}`, key: `deliver_${t.taskId}`, duration: 0 });
    const deliveredFiles: { label: string; filename: string }[] = [];
    for (const item of present) {
      const bytes = await downloadTaskFileBytes(t.taskId, item.a.path);
      await writeFile(baseDir, `${relDir}/${item.w.out}`, bytes);
      deliveredFiles.push({ label: item.w.label, filename: item.w.out });
    }
    message.success({ content: `已保存到输出目录：${t.inputName}`, key: `deliver_${t.taskId}` });

    updateActiveBatchById(batchId, (bb) => {
      const tasks = [...bb.tasks];
      tasks[taskIdx] = { ...tasks[taskIdx], deliveredDir: relDir, deliveredFiles };
      return { ...bb, tasks };
    });
  }

  function startPollingForTask(batchId: string, taskIdx: number, taskId: string) {
    stopPolling();
    activeBackendTaskIdRef.current = taskId;
    pollOnce(batchId, taskIdx, taskId, 0);
  }

  async function pollOnce(batchId: string, taskIdx: number, taskId: string, logOffset: number) {
    if (activeBackendTaskIdRef.current !== taskId) return;
    try {
      const [st, log] = await Promise.all([getStatus(taskId), getLog(taskId, logOffset)]);
      // update task status
      updateActiveBatchById(batchId, (bb) => {
        const tasks = [...bb.tasks];
        const prev = tasks[taskIdx];
        tasks[taskIdx] = {
          ...prev,
          taskId,
          state: uiStateFromBackend(st.state),
          progress: st.progress,
          stageName: st.stage_name,
          message: st.message,
          workDir: st.work_dir,
        };
        return { ...bb, tasks, currentTaskIndex: taskIdx };
      });

      // drawer log auto append if drawer is showing this task
      if (drawerOpen && drawerTaskIndex === taskIdx && log?.content) {
        setDrawerLog((prev) => (prev + log.content).slice(-20000));
        setDrawerLogOffset(log.next_offset || logOffset + (log.content?.length || 0));
      }

      if (st.state !== "running") {
        stopPolling();
        await finalizeTask(batchId, taskIdx, taskId, st);
        return;
      }
      pollingTimer.current = setTimeout(() => pollOnce(batchId, taskIdx, taskId, log.next_offset || 0), pollingMs);
    } catch (err: any) {
      pollingTimer.current = setTimeout(() => pollOnce(batchId, taskIdx, taskId, logOffset), Math.max(pollingMs * 2, 2000));
    }
  }

  async function openTaskDrawer(taskIdx: number, initialTab: string = "quality") {
    setDrawerTaskIndex(taskIdx);
    setDrawerInitialTab(initialTab);
    setDrawerOpen(true);
    setDrawerLog("");
    setDrawerLogOffset(0);
    if (!activeBatch) return;
    const t = activeBatch.tasks[taskIdx];
    if (t.taskId) {
      setDrawerLogLoading(true);
      try {
        const log = await getLog(t.taskId, 0);
        setDrawerLog(log.content || "");
        setDrawerLogOffset(log.next_offset || 0);
      } catch {
        setDrawerLog("");
        setDrawerLogOffset(0);
      } finally {
        setDrawerLogLoading(false);
      }
    }
  }

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
  const wizard = (
        <Content style={{ padding: 24 }}>
          <Space direction="vertical" size="large" style={{ width: "100%" }}>

            <Card>
          <Steps
            current={wizardStep}
            items={[
              { title: "添加素材" },
              { title: "交付设置" },
              { title: "确认开始" },
            ]}
                />
            </Card>

        {wizardStep === 0 && (
          <Card title="Step 1：添加素材" extra={<Text type="secondary">顺序即处理顺序（可调整）</Text>}>
            <Space wrap style={{ marginBottom: 12 }}>
              <Upload
                directory
                multiple
                accept="video/*,audio/*"
                showUploadList={false}
                disabled={wizardUploading}
                customRequest={handleAddUpload}
              >
                <Button icon={<FolderOpenOutlined />}>选择文件夹…</Button>
              </Upload>
              <Text type="secondary">支持拖拽文件/文件夹；批量建议用“选择文件夹”。</Text>
            </Space>
            <Upload.Dragger
              multiple
              accept="video/*,audio/*"
              showUploadList={false}
              disabled={wizardUploading}
              customRequest={handleAddUpload}
              directory
              openFileDialogOnClick={false}
              style={{ marginBottom: 16 }}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">拖拽文件或文件夹到这里</p>
              <p className="ant-upload-hint">会自动上传并添加到列表</p>
            </Upload.Dragger>

            {wizardTasks.length === 0 ? (
              <Alert type="info" showIcon message="还没有视频。先把要处理的视频加进来吧。" />
            ) : (
                <List
                bordered
                dataSource={wizardTasks}
                renderItem={(item, idx) => (
                  <List.Item
                    actions={[
                      <Button key="up" size="small" disabled={idx === 0} onClick={() => moveTask(idx, -1)}>
                        上移
                      </Button>,
                      <Button key="down" size="small" disabled={idx === wizardTasks.length - 1} onClick={() => moveTask(idx, 1)}>
                        下移
                      </Button>,
                      <Button key="rm" danger size="small" onClick={() => removeTask(idx)}>
                        删除
                      </Button>,
                    ]}
                  >
                      <Space>
                      <Tag>{twoDigitIndex(idx + 1)}</Tag>
                      <Text>{item.inputName}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
            )}

            <Divider />
            <div style={{ position: "fixed", right: 40, bottom: 40, zIndex: 1000 }}>
              <Space>
                <Button type="primary" disabled={wizardTasks.length === 0} onClick={() => setWizardStep(1)}>
                  下一步
                </Button>
              </Space>
            </div>
            </Card>
        )}

        {wizardStep === 1 && (
          <Card
            title={
              <Space align="center" size="small">
                <Text>交付设置</Text>
                <Tag color="blue">{mode === "lite" ? "轻量模式" : mode === "quality" ? "质量模式" : "在线模式"}</Tag>
              </Space>
            }
            extra={
              <Space wrap align="center" size="small">
                <Button size="small" onClick={() => setRoute("advanced")}>
                  打开高级设置
                </Button>
                <Button
                  size="small"
                  onClick={() => {
                    const v = form.getFieldValue("mt_topic");
                    setMtTopicDraft(String(v || ""));
                    setMtTopicModalOpen(true);
                  }}
                >
                  翻译主题
                </Button>
                <Button size="small" onClick={openGlossaryModal}>
                  术语
                </Button>
                <Space size="small" align="center">
                  <Text type="secondary">校审</Text>
                  <Switch checked={reviewEnabled} onChange={(v) => setReviewEnabled(v)} checkedChildren="开" unCheckedChildren="关" />
                </Space>
              </Space>
            }
          >
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Form form={form} layout="vertical">
                <Form.Item label="批次名">
                  <Input value={batchName} onChange={(e) => setBatchName(e.target.value)} placeholder="批次-20251231-1030" />
                </Form.Item>

                <Form.Item label="输出位置" extra="成片与字幕保存位置">
                  <Space.Compact style={{ width: "100%" }}>
                    <Input value={outputDir} readOnly placeholder="点击右侧按钮选择文件夹…" />
                    <Button icon={<FolderOpenOutlined />} onClick={chooseOutputDir}>
                      选择文件夹
                    </Button>
                    {outputDir && (
                      <Button onClick={() => openPath(outputDir)}>
                        打开
                      </Button>
                    )}
                  </Space.Compact>
                </Form.Item>

                <Form.Item label="原片字幕">
                  <Radio.Group
                    value={subtitleSource}
                    onChange={(e) => setSubtitleSource(e.target.value)}
                    optionType="button"
                    buttonStyle="solid"
                  >
                    <Radio.Button value="has">有</Radio.Button>
                    <Radio.Button value="none">无</Radio.Button>
                  </Radio.Group>
                </Form.Item>

                {subtitleSource === "has" ? (
                  <Card size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Alert type="info" showIcon message="拖动矩形定位字幕区域（自动启用擦除）" />

                      <Row gutter={12}>
                        <Col span={24}>
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Row gutter={12}>
                              <Col span={12}>
                                <Text>位置（y）</Text>
                                <Slider
                                  min={0}
                                  max={Math.max(0, 1 - regionPickerRect.h)}
                                  step={0.001}
                                  value={regionPickerRect.y}
                                  onChange={(v) => setRegionRectSafe({ y: Number(v) })}
                                />
                              </Col>
                              <Col span={12}>
                                <Text>字幕字号</Text>
                                <Slider
                                  min={10}
                                  max={60}
                                  step={1}
                                  value={regionPickerSampleFontSize}
                                  onChange={(v) => setFinalSubtitleFontSize(Number(v || 18))}
                                />
                              </Col>
                            </Row>
                            <Row gutter={12}>
                              <Col span={12}>
                                <Text>宽度（w）</Text>
                                <Slider min={0.05} max={1.0} step={0.001} value={regionPickerRect.w} onChange={(v) => setRegionRectSafe({ w: Number(v) })} />
                              </Col>
                              <Col span={12}>
                                <Text>高度（h）</Text>
                                <Slider min={0.03} max={0.6} step={0.001} value={regionPickerRect.h} onChange={(v) => setRegionRectSafe({ h: Number(v) })} />
                              </Col>
                            </Row>
                          </Space>
                        </Col>
                      </Row>

                      <div
                        ref={regionPickerFrameRef}
                        style={{
                          position: "relative",
                          width: "100%",
                          maxWidth: "100%",
                          minHeight: 360,
                          height: "60vh",
                          margin: "0 auto",
                          background: "#000",
                          borderRadius: 8,
                          overflow: "hidden",
                          userSelect: "none",
                        }}
                      >
                        {regionPickerVideoPath ? (
                          <video
                            ref={regionPickerVideoRef}
                            src={regionPickerVideoPath}
                            controls
                            preload="metadata"
                            style={{
                              width: "100%",
                              height: "100%",
                              display: "block",
                              objectFit: "contain",
                            }}
                            onLoadedMetadata={() => {
                              setRegionPickerVideoReady(true);
                              setRegionPickerVideoError("");
                              const v = regionPickerVideoRef.current;
                              if (v) {
                                setRegionPickerVideoInfo((prev) => ({
                                  ...prev,
                                  duration: Number.isFinite(v.duration) ? v.duration : undefined,
                                  w: v.videoWidth || undefined,
                                  h: v.videoHeight || undefined,
                                }));
                              }
                            }}
                            onError={() => {
                              setRegionPickerVideoReady(false);
                              const v = regionPickerVideoRef.current as any;
                              const code = v?.error?.code;
                              const msg = v?.error?.message;
                              setRegionPickerVideoError(`视频加载失败（code=${code || "?"}${msg ? `, ${msg}` : ""}）。可点上方“选择预览视频…”重试。`);
                            }}
                          />
                        ) : (
                          <div style={{ padding: 18 }}>
                            <Text type="secondary">请先选择一个预览视频。</Text>
                          </div>
                        )}
                        <div
                          style={{
                            position: "absolute",
                            left: `${regionPickerVideoBox.x + regionPickerRect.x * regionPickerVideoBox.w}px`,
                            top: `${regionPickerVideoBox.y + regionPickerRect.y * regionPickerVideoBox.h}px`,
                            width: `${regionPickerRect.w * regionPickerVideoBox.w}px`,
                            height: `${regionPickerRect.h * regionPickerVideoBox.h}px`,
                            border: "2px solid #faad14",
                            background: "rgba(250, 173, 20, 0.15)",
                            pointerEvents: "none",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            textAlign: "center",
                          }}
                        >
                          <div
                            style={{
                              color: "#fff",
                              fontSize: regionPickerSampleFontSize * regionPickerVideoScale,
                              fontWeight: 400,
                              lineHeight: 1.0,
                              textAlign: "center",
                              textShadow: "0 0 0 rgba(0,0,0,0.6)",
                              whiteSpace: "pre-wrap",
                              maxWidth: "100%",
                            }}
                          >
                            {regionPickerSampleText}
                          </div>
                        </div>
                      </div>

                      <Space align="center" style={{ width: "100%", justifyContent: "center" }}>
                        <Button size="large" type="primary" onClick={saveSubtitleSettings}>
                          保存设置
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                ) : (
                  <Card size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Space wrap>
                        <Button
                          size="small"
                          onClick={() =>
                            form.setFieldsValue({
                              sub_font_size: 18,
                              sub_margin_v: 24,
                              sub_outline: 1,
                              sub_alignment: 2,
                            })
                          }
                        >
                          恢复推荐默认
                        </Button>
                      </Space>
                      <Row gutter={12}>
                        <Col span={6}>
                          <Form.Item label="字号" name="sub_font_size">
                            <InputNumber style={{ width: "100%" }} min={10} max={40} />
                          </Form.Item>
                        </Col>
                        <Col span={6}>
                          <Form.Item label="底部边距（px）" name="sub_margin_v">
                            <InputNumber style={{ width: "100%" }} min={0} max={120} />
                          </Form.Item>
                        </Col>
                        <Col span={6}>
                          <Form.Item label="描边" name="sub_outline">
                            <InputNumber style={{ width: "100%" }} min={0} max={6} />
                          </Form.Item>
                        </Col>
                        <Col span={6}>
                          <Form.Item label="对齐" name="sub_alignment">
                            <Select
                              options={[
                                { label: "底部居中（推荐）", value: 2 },
                                { label: "底部左侧", value: 1 },
                                { label: "底部右侧", value: 3 },
                              ]}
                            />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Space align="center" style={{ width: "100%", justifyContent: "center" }}>
                        <Button size="large" type="primary" onClick={saveSubtitleSettings}>
                          保存设置
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                )}
              </Form>

              <div style={{ position: "fixed", right: 40, bottom: 40, zIndex: 1000 }}>
                <Space>
                  <Button onClick={() => setWizardStep(0)}>上一步</Button>
                  <Button
                    type="primary"
                    onClick={() => {
                      if (hasUnsavedHasSettings()) {
                        Modal.confirm({
                          title: "应用有字幕配置？",
                          content: "检测到有字幕设置已修改但未保存。是否应用当前配置继续？",
                          okText: "应用并继续",
                          cancelText: "不应用",
                          centered: true,
                          onOk: () => {
                            saveSubtitleSettings();
                            applySavedSubtitleSettings();
                            setWizardStep(2);
                          },
                          onCancel: () => {
                            applySavedSubtitleSettings();
                            setWizardStep(2);
                          },
                        });
                        return;
                      }
                      applySavedSubtitleSettings();
                      setWizardStep(2);
                    }}
                  >
                    下一步
                  </Button>
                </Space>
              </div>
            </Space>
            </Card>
        )}

        {wizardStep === 2 && (
          <Card title="Step 3：确认并开始">
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert
                  type="info"
                  showIcon
                message="一次只处理一个视频（串行队列最稳）。失败不会阻塞，会自动进入下一个。"
              />
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="批次名">{batchName}</Descriptions.Item>
                <Descriptions.Item label="视频数量">{wizardTasks.length}</Descriptions.Item>
                <Descriptions.Item label="输出位置">{outputDir || "未选择（可继续，稍后手动下载交付物）"}</Descriptions.Item>
              <Descriptions.Item label="模式">{modeLabel(mode)}</Descriptions.Item>
              </Descriptions>
              <div style={{ position: "fixed", right: 40, bottom: 40, zIndex: 1000 }}>
                <Space>
                  <Button onClick={() => setWizardStep(1)}>上一步</Button>
                  <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => createBatchAndGo(true)}>
                    开始处理
                  </Button>
                </Space>
              </div>
            </Space>
          </Card>
        )}
      </Space>
    </Content>
  );

  // ---------------------------
  // Render: Workbench
  // ---------------------------
  const workbench = (
    <Content style={{ padding: 16 }}>
      {batches.length === 0 ? (
        <Card>
          <Alert
            type="info"
            showIcon
            message="还没有批次"
            description="点击「新建批次」开始。"
            action={
              <Button type="primary" onClick={resetWizard}>
                新建批次
              </Button>
            }
          />
        </Card>
      ) : (
        <Card title="任务中心" extra={<Text type="secondary">按批次管理：点开批次 → 表格查看该批次的视频任务</Text>}>
          <Collapse
            accordion={false}
            defaultActiveKey={activeBatchId ? [activeBatchId] : undefined}
            items={batches.map((b) => {
              const counts = batchCounts(b);
              const runningIdx = b.tasks.findIndex((t) => t.state === "running");
              const current = runningIdx >= 0 ? b.tasks[runningIdx] : null;

              const columns = [
                { title: "序号", dataIndex: "index", width: 70, render: (v: number) => <Tag>{twoDigitIndex(v)}</Tag> },
                { title: "文件", dataIndex: "inputName", ellipsis: true },
                {
                  title: "状态",
                  dataIndex: "state",
                  width: 110,
                  render: (s: UiTaskState) => <Tag color={tagColorForUiState(s)}>{taskStateLabel(s)}</Tag>,
                },
                {
                  title: "进度",
                  dataIndex: "progress",
                  width: 160,
                  render: (_: any, t: BatchTask) =>
                    t.state === "running" ? <Progress percent={t.progress || 0} size="small" /> : <Text type="secondary">-</Text>,
                },
                {
                  title: "操作",
                  key: "actions",
                  width: 220,
                  render: (_: any, t: BatchTask) => (
                    <Space>
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          setActiveBatchId(b.id);
                          openTaskDrawer(b.tasks.findIndex((x) => x.index === t.index), "quality");
                        }}
                      >
                        详情
                      </Button>
                      {t.state === "completed" && (b.params?.review_enabled ?? true) !== false ? (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => {
                            setActiveBatchId(b.id);
                            openTaskDrawer(b.tasks.findIndex((x) => x.index === t.index), "review");
                          }}
                        >
                          校审
                        </Button>
                      ) : null}
                      {t.state === "failed" || t.state === "paused" ? (
                        <>
                          <Button
                            size="small"
                            type="link"
                            onClick={() => {
                              setActiveBatchId(b.id);
                              openTaskDrawer(b.tasks.findIndex((x) => x.index === t.index), "quality");
                            }}
                          >
                            查看原因
                          </Button>
                          <Button
                            size="small"
                            type="link"
                            onClick={() => {
                              setActiveBatchId(b.id);
                              const taskIndex = b.tasks.findIndex((x) => x.index === t.index);
                              setTimeout(() => resumeTaskInPlace(taskIndex, "mt"), 0);
                            }}
                          >
                            从上次继续
                          </Button>
                        </>
                      ) : null}
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          const relDir = `${safeStem(b.name)}-${twoDigitIndex(t.index)}`;
                          if (t.deliveredDir) {
                            const base = b.outputDir || "";
                            if (base) return openPath(`${base}/${t.deliveredDir}`);
                            return openDefaultOutputsFolder(t.deliveredDir);
                          }
                          deliverTaskToOutputDir(b.id, b.tasks.findIndex((x) => x.index === t.index))
                            .then(() => {
                              if (b.outputDir) return openPath(`${b.outputDir}/${relDir}`);
                              return openDefaultOutputsFolder(relDir);
                            })
                            .catch(() => openBatchOutputFolder(b));
                        }}
                      >
                        交付
                      </Button>
                    </Space>
                  ),
                },
              ];

              return {
                key: b.id,
                label: (
                  <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
                    <Space wrap>
                      <Text strong>{b.name}</Text>
                      <Tag>{modeLabel(b.mode)}</Tag>
                      <Tag>{batchStateLabel(b.state)}</Tag>
                      <Tag>总 {counts.total}</Tag>
                      <Tag color="green">完成 {counts.done}</Tag>
                      {counts.failed > 0 && <Tag color="red">失败 {counts.failed}</Tag>}
                      {counts.pending > 0 && <Tag>待处理 {counts.pending}</Tag>}
                    </Space>
                    <Space wrap>
                      {(() => {
                        const allDone = b.tasks.every((t) => ["completed", "failed", "cancelled"].includes(t.state));
                        const label = allDone
                          ? "已完成"
                          : b.state === "running"
                            ? "暂停"
                            : b.state === "paused"
                              ? "继续"
                              : b.state === "queued"
                                ? "排队中"
                                : "开始";
                        const disabled = allDone || b.state === "queued";
                        return (
                          <Button
                            size="small"
                            type="primary"
                            disabled={disabled}
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveBatchId(b.id);
                              if (disabled) return;
                              if (b.state === "running") return pauseQueueById(b.id);
                              if (b.state === "paused") return resumeQueueById(b.id);
                              return startQueue(b.id);
                            }}
                          >
                            {label}
                          </Button>
                        );
                      })()}
                      <Button
                        size="small"
                        icon={<FolderOpenOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          openBatchOutputFolder(b);
                        }}
                      >
                        交付
                      </Button>
                    </Space>
                  </Space>
                ),
                children: (
                  <Card size="small" style={{ marginTop: 8 }}>
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Space wrap>
                        <Text type="secondary">创建时间：{new Date(b.createdAt).toLocaleString()}</Text>
                        <Text type="secondary">输出目录：{b.outputDir || "（未选择）"}</Text>
                      </Space>
                      <Table
                  size="small"
                        rowKey={(r: any) => String(r.index)}
                        pagination={false}
                        columns={columns as any}
                        dataSource={b.tasks as any}
                      />
                      </Space>
                  </Card>
                ),
              };
            })}
          />
        </Card>
      )}

      <Drawer
        title={activeBatch && drawerTaskIndex >= 0 ? `任务详情：${activeBatch.tasks[drawerTaskIndex].inputName}` : "任务详情"}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={720}
        destroyOnClose={false}
      >
        {activeBatch && drawerTaskIndex >= 0 ? (
          <TaskDrawerContent
            batch={activeBatch}
            taskIndex={drawerTaskIndex}
            initialTab={drawerInitialTab}
            onOpenOutput={(rel) => {
              if (rel) {
                if (activeBatch.outputDir) return openPath(`${activeBatch.outputDir}/${rel}`);
                return openDefaultOutputsFolder(rel);
              }
              return openBatchOutputFolder(activeBatch);
            }}
            logText={drawerLog}
            logLoading={drawerLogLoading}
            onResume={(resumeFrom) => resumeTaskInPlace(drawerTaskIndex, resumeFrom)}
            onRunReview={(lang) => runReviewAndPoll(drawerTaskIndex, lang)}
            onApplyReview={(action, use) => applyReviewAndRefresh(drawerTaskIndex, action, use)}
            onExportDiagnostic={(opts) => exportDiagnosticZipForTask(drawerTaskIndex, opts)}
            onCleanup={(idx) => openCleanupDialog(idx)}
          />
        ) : (
          <Text type="secondary">未选择任务</Text>
        )}
      </Drawer>
    </Content>
  );

  const history = (
    <Content style={{ padding: 16 }}>
      <Card
        title="历史记录"
        extra={<Text type="secondary">批次列表会保存在本机（localStorage）。重启仍在；清理浏览器数据/重装应用会丢失。</Text>}
      >
        {batches.length === 0 ? (
          <Empty description="暂无历史记录">
            <Button type="primary" onClick={resetWizard}>新建批次</Button>
          </Empty>
        ) : (
          <Collapse
            accordion={false}
            items={batches.map((b) => {
              const counts = batchCounts(b);
              const columns = [
                { title: "序号", dataIndex: "index", width: 70, render: (v: number) => <Tag>{twoDigitIndex(v)}</Tag> },
                { title: "文件", dataIndex: "inputName", ellipsis: true },
                {
                  title: "状态",
                  dataIndex: "state",
                  width: 110,
                  render: (s: UiTaskState) => <Tag color={tagColorForUiState(s)}>{taskStateLabel(s)}</Tag>,
                },
                {
                  title: "交付",
                  dataIndex: "deliveredDir",
                  width: 90,
                  render: (_: any, t: BatchTask) => (
                    <Button size="small" onClick={() => openDeliveredDirForTask(b, t)}>
                      打开
                    </Button>
                  ),
                },
              ];
              return {
                key: b.id,
                label: (
                  <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
                    <Space wrap>
                      <Text strong>{b.name}</Text>
                      <Tag>{modeLabel(b.mode)}</Tag>
                      <Tag>{batchStateLabel(b.state)}</Tag>
                      <Tag>总 {counts.total}</Tag>
                      <Tag color="green">完成 {counts.done}</Tag>
                      {counts.failed > 0 && <Tag color="red">失败 {counts.failed}</Tag>}
                      {counts.pending > 0 && <Tag>待处理 {counts.pending}</Tag>}
                    </Space>
                    <Space wrap>
                      <Button
                        size="small"
                        type="primary"
                        onClick={(e) => {
                          e.stopPropagation();
                          openBatchOutputFolder(b);
                        }}
                      >
                        打开
                      </Button>
                      <Popconfirm
                        title="删除该批次？"
                        description="仅删除本地记录，不会影响后端文件。"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={() => {
                          setBatches((prev) => prev.filter((x) => x.id !== b.id));
                          if (activeBatchIdRef.current === b.id) {
                            setActiveBatchId("");
                          }
                        }}
                      >
                        <Button size="small" danger onClick={(e) => e.stopPropagation()}>
                          删除记录
                        </Button>
                      </Popconfirm>
                    </Space>
                  </Space>
                ),
                children: (
                  <Card size="small" style={{ marginTop: 8 }}>
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      <Space wrap>
                        <Text type="secondary">创建时间：{new Date(b.createdAt).toLocaleString()}</Text>
                        <Text type="secondary">输出目录：{b.outputDir || "（未选择）"}</Text>
                      </Space>
                      <Table
                        size="small"
                        rowKey={(r: any) => String(r.index)}
                        pagination={false}
                        columns={columns as any}
                        dataSource={b.tasks as any}
                      />
              </Space>
            </Card>
                ),
              };
            })}
          />
        )}
      </Card>
    </Content>
  );

  const modeSelect = (
    <Content style={{ padding: 16 }}>
      <Card title="模式选择" extra={<Text type="secondary">选择后会作为“默认模式”，新建批次时自动使用</Text>}>
        <Row gutter={[12, 12]}>
          {[
            {
              key: "lite" as const,
              title: "轻量模式",
              icon: <ThunderboltOutlined style={{ fontSize: 22, color: "#1677ff" }} />,
              desc: "本地离线，资源占用低，稳定交付（推荐）。",
            },
            {
              key: "quality" as const,
              title: "质量模式",
              icon: <CrownOutlined style={{ fontSize: 22, color: "#722ed1" }} />,
              desc: "更高质量（更慢），对算力要求更高。",
            },
            {
              key: "online" as const,
              title: "在线模式",
              icon: <CloudOutlined style={{ fontSize: 22, color: "#13c2c2" }} />,
              desc: "依赖在线服务与密钥（更不稳定，谨慎使用）。",
            },
          ].map((m) => {
            const available = availableModes.includes(m.key);
            const selected = mode === m.key;
            return (
              <Col key={m.key} span={8}>
                <Card
                  hoverable={available}
                  style={{
                    borderColor: selected ? "#1677ff" : undefined,
                    opacity: available ? 1 : 0.5,
                    cursor: available ? "pointer" : "not-allowed",
                    height: "100%",
                  }}
                  onClick={() => {
                    if (!available) return;
                    setMode(m.key);
                    const next = { ...uiPrefs, defaultMode: m.key };
                    setUiPrefs(next);
                    saveUiPrefs(next);
                    message.success(`已选择：${m.title}`);
                  }}
                >
                  <Space direction="vertical" size="small" style={{ width: "100%" }}>
                    <Space align="center" style={{ justifyContent: "space-between", width: "100%" }}>
                      <Space align="center">
                        {m.icon}
                        <Text strong>{m.title}</Text>
                      </Space>
                      {selected && <Tag color="blue">当前</Tag>}
                      {!available && <Tag>不可用</Tag>}
                    </Space>
                    <Text type="secondary">{m.desc}</Text>
              </Space>
            </Card>
              </Col>
            );
          })}
        </Row>
      </Card>
    </Content>
  );

  function stageOfToggle(key: string): string {
    // 按“阶段”分组（中文友好）
    if (key.endsWith("_endpoint") || key.endsWith("_api_key") || key.startsWith("llm_")) return "开发者";
    if (key === "allow_gpu" || key === "allow_heavy_models" || key === "offline") return "开发者";
    if (key.startsWith("asr_") || key === "vad_enable" || key === "denoise") return "语音识别";
    if (
      key.startsWith("mt_") ||
      key === "glossary_prompt_enable" ||
      key === "glossary_placeholder_enable" ||
      key === "meaning_split_enable" ||
      key === "entity_protect_enable" ||
      key === "sentence_unit_enable" ||
      key.startsWith("qe_") ||
      key.startsWith("tra_")
    )
      return "翻译";
    if (key.startsWith("tts_")) return "配音";
    if (
      key.startsWith("subtitle_") ||
      key.startsWith("display_") ||
      key.startsWith("mux_") ||
      key.startsWith("bgm_") ||
      key === "bilingual_srt"
    )
      return "合成";
    return "开发者";
  }

  type RiskLevel = "低" | "中" | "高" | "实验" | "未知";

  function riskTagColor(level: RiskLevel) {
    if (level === "低") return "green";
    if (level === "中") return "gold";
    if (level === "高") return "orange";
    if (level === "实验") return "red";
    return "default";
  }

  const toggleMeta: Record<
    string,
    { label: string; desc?: string; risk?: RiskLevel; riskHint?: string; recommend?: "建议开启" | "建议关闭" | "按需" }
  > = {
    // ASR/audio
    offline: { label: "离线模式", desc: "禁止运行时联网下载（更稳）。", risk: "低", recommend: "建议开启", riskHint: "开启后更稳；若本地缺模型会直接报错提示手动放置模型。" },
    vad_enable: { label: "人声检测", desc: "自动跳过长静音，改善切分。", risk: "中", recommend: "按需", riskHint: "静音多、断句怪时开启；若漏词增多请关闭。" },
    denoise: { label: "去噪", desc: "减弱底噪与嘶声。", risk: "中", recommend: "按需", riskHint: "底噪明显时开启；若音色变闷请关闭。" },
    asr_normalize_enable: { label: "识别文本净化", desc: "清理乱码、空白、重复标点。", risk: "低", recommend: "建议开启", riskHint: "几乎不改语义，只让文本更干净。" },
    asr_preprocess_enable: { label: "识别前音频预处理", desc: "响度与滤波校正。", risk: "中", recommend: "按需", riskHint: "音量忽大忽小/杂音多时开启。" },
    asr_merge_short_enable: { label: "合并极短片段", desc: "减少一两个字的碎片字幕。", risk: "中", recommend: "按需", riskHint: "字幕过碎时开启；若合并过头请关闭。" },
    asr_llm_fix_enable: { label: "识别保守纠错", desc: "修正明显同音错字。", risk: "高", recommend: "按需", riskHint: "错字多时开启；可能引入改写，建议抽样看结果。" },
    // MT/text
    sentence_unit_enable: { label: "句子单元（合并再拆回）", desc: "提升短句连贯性。", risk: "中", recommend: "按需", riskHint: "字幕太碎/翻译跳跃时开启。" },
    entity_protect_enable: { label: "专名保护", desc: "人名/地名统一译法。", risk: "中", recommend: "按需", riskHint: "专名频繁错译时开启；专名少则无感。" },
    mt_pause_before_translate: { label: "译前暂停（可编辑术语）", desc: "生成术语文件，编辑后从翻译继续。", risk: "低", recommend: "按需", riskHint: "不会破坏流程，只是多一步人工确认（适合重要交付）。" },
    meaning_split_enable: { label: "语义切句", desc: "超长句自动拆分。", risk: "高", recommend: "按需", riskHint: "长句导致译文过长/配音过快时开启。" },
    glossary_prompt_enable: { label: "术语提示（软约束）", desc: "在翻译中提示术语。", risk: "低", recommend: "按需", riskHint: "术语一致性要求高时开启。" },
    glossary_placeholder_enable: { label: "术语占位符保护", desc: "强约束术语不被改写。", risk: "中", recommend: "按需", riskHint: "术语非常关键时开启；偶尔会让句子不自然。" },
    qe_enable: { label: "质量评审", desc: "交付前自动找问题。", risk: "高", recommend: "按需", riskHint: "更慢，重要交付再开。" },
    qe_embed_enable: { label: "语义相似度评审", desc: "更慢、更吃资源。", risk: "实验", recommend: "按需", riskHint: "更偏实验/排查。建议只在开发排障或评测时开启。" },
    qe_backtranslate_enable: { label: "回译评审", desc: "更慢、更吃资源。", risk: "实验", recommend: "按需", riskHint: "更偏实验/评测用途。普通交付不建议开启。" },
    tra_enable: { label: "多步翻译", desc: "更自然但更慢。", risk: "高", recommend: "按需", riskHint: "文风要求高时开启；时效优先时关闭。" },
    mt_json_enable: { label: "结构化翻译", desc: "更严格的结构化输出（可能更慢）。", risk: "中", recommend: "按需", riskHint: "可能提升稳定性，但也可能变慢或变生硬。建议遇到翻译格式问题再开。" },
    mt_topic_auto_enable: { label: "自动主题提示", desc: "自动推断主题以辅助翻译。", risk: "中", recommend: "按需", riskHint: "对内容敏感，可能有提升也可能回退。建议按需开启并抽样复核。" },
    tra_json_enable: { label: "多步翻译：结构化输出", desc: "为多步翻译提供结构化中间输出。", risk: "实验", recommend: "按需", riskHint: "更偏实验/评测。普通交付不建议开启。" },
    tra_auto_enable: { label: "多步翻译：自动模式", desc: "自动选择多步翻译策略。", risk: "实验", recommend: "按需", riskHint: "更偏实验/评测。普通交付不建议开启。" },
    // Subtitles / deliverables
    subtitle_postprocess_enable: { label: "字幕后处理", desc: "优化阅读速度与断行。", risk: "中", recommend: "按需", riskHint: "阅读速度告警/行太长时开启。" },
    subtitle_wrap_enable: { label: "字幕软换行", desc: "更易读（可能改变行数）。", risk: "中", recommend: "按需", riskHint: "对交付观感更好；但会改变行结构。建议需要更美观时开启。" },
    subtitle_cps_fix_enable: { label: "阅读速度补偿", desc: "尝试降低阅读速度告警。", risk: "高", recommend: "按需", riskHint: "会改动时间轴，风险更高。建议仅用于明确出现阅读速度问题的素材。" },
    display_srt_enable: { label: "生成显示版字幕", desc: "更适合观看的版本。", risk: "中", recommend: "按需", riskHint: "生成额外版本通常安全；但若后续用于封装，则可能影响最终字幕外观。" },
    display_use_for_embed: { label: "封装使用显示版字幕", desc: "成片字幕采用显示版样式。", risk: "高", recommend: "按需", riskHint: "字幕外观会变化，建议先预览再开。" },
    display_merge_enable: { label: "显示字幕：合并短段", desc: "让显示字幕更连贯。", risk: "中", recommend: "按需", riskHint: "可能更顺，但也可能合并不当。建议抽样检查。" },
    display_split_enable: { label: "显示字幕：拆分长行", desc: "长行拆成更易读的多行。", risk: "低", recommend: "按需", riskHint: "显示字幕过长时开启。" },
    erase_subtitle_enable: { label: "硬字幕擦除", desc: "画面里有烧录字幕时使用。", risk: "高", recommend: "按需", riskHint: "可能擦不干净或伤画面。建议只在确实需要去除烧录字幕时开启。" },
    // TTS
    tts_script_enable: { label: "配音稿分离", desc: "字幕稿 vs 朗读稿分离。", risk: "中", recommend: "按需", riskHint: "更像“提质能力”，通常安全，但会增加额外产物与处理时间。" },
    tts_script_strict_clean_enable: { label: "严格清洗配音稿", desc: "URL/邮箱/单位等规范。", risk: "中", recommend: "按需", riskHint: "可能提升可读性；也可能删改你不想删的细节。建议遇到 TTS 读不出/读错时开启。" },
    tts_fit_enable: { label: "配音超时裁剪", desc: "解决读不完的问题。", risk: "高", recommend: "按需", riskHint: "会改写朗读稿；配音总超时再开。" },
    tts_plan_enable: { label: "配音语速规划与停顿", desc: "让语速更稳。", risk: "高", recommend: "按需", riskHint: "会更慢；重要交付再开。" },
    // Mix
    bgm_mix_enable: { label: "保留背景音并混音", desc: "更像成片效果（更慢）。", risk: "高", recommend: "按需", riskHint: "会显著增加耗时与复杂度。建议对“成片听感”有要求时开启。" },
    bgm_duck_enable: { label: "背景音自动压低", desc: "配音出现时自动压低背景音。", risk: "中", recommend: "按需", riskHint: "通常安全；但不同素材可能压得不舒服。" },
    bgm_loudnorm_enable: { label: "背景音响度归一", desc: "让背景音更平稳。", risk: "中", recommend: "按需", riskHint: "通常安全；但可能改变原始动态。" },
    // Perf
    allow_gpu: { label: "允许使用显卡加速", desc: "如有显卡则尝试使用。", risk: "低", recommend: "按需", riskHint: "一般建议开启（若驱动/环境稳定）。若遇到显卡相关报错，可关闭以求稳。" },
    allow_heavy_models: { label: "允许使用更重模型", desc: "更慢更吃资源。", risk: "中", recommend: "按需", riskHint: "可能提升质量，但更慢。建议在高配机器上按需开启。" },

    // DevOnly / debug / workflow / placeholder (make them meaningful in Chinese)
    asr_preprocess_loudnorm: { label: "识别预处理：响度归一", desc: "仅在开启“识别前音频预处理”后生效。", risk: "中", recommend: "按需", riskHint: "可能提升音量一致性，但会更慢；用于排障/专项素材更合适。" },
    asr_merge_save_debug: { label: "保存识别合并调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
    asr_llm_fix_save_debug: { label: "保存识别纠错调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
    meaning_split_save_debug: { label: "保存语义切句调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
    tts_fit_save_raw: { label: "保存配音裁剪原始数据", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
    tra_save_debug: { label: "保存多步翻译调试文件", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "不会直接提升交付体验；仅用于研发/排障。" },
    qe_save_report: { label: "保存质量评审详细报告", desc: "只用于排查（会额外写文件）。", risk: "实验", recommend: "按需", riskHint: "普通交付不需要；用于定位问题更合适。" },
    mt_skip_if_present: { label: "复用已有翻译（如果存在）", desc: "工作流/加速项：复用旧结果。", risk: "高", recommend: "按需", riskHint: "可能导致结果不可比（参数变了但复用旧翻译）。仅用于开发排障/重复跑测试。" },
    skip_tts: { label: "跳过配音（只产出字幕）", desc: "用于只交付字幕的场景。", risk: "中", recommend: "按需", riskHint: "不会生成成片与配音，这是预期行为。普通交付成片时不要开启。" },
    diarization: { label: "说话人分离（占位/不建议）", desc: "当前更偏占位/专项能力。", risk: "实验", recommend: "按需", riskHint: "结论不可泛化；仅用于开发探索。" },
    // glossary_* 已在上方以“术语提示/占位符保护”形式定义，避免重复键
    dedupe: { label: "去重（占位）", desc: "轻量模式目前不生效（脚本不读取该参数）。", risk: "未知", recommend: "按需", riskHint: "建议不要在交付中依赖它；后续可选择接入或移除。" },
  };

  const paramMeta: Record<
    string,
    { label: string; desc?: string; risk?: RiskLevel; riskHint?: string; recommend?: "建议默认" | "按需"; unit?: string }
  > = {
    sample_rate: {
      label: "音频采样率",
      desc: "一般不需要改；少数兼容场景可调整。",
      unit: "Hz",
      risk: "中",
      recommend: "建议默认",
      riskHint: "采样率越高文件更大；过低可能影响识别效果。默认 16000 较稳。",
    },
    whispercpp_threads: {
      label: "识别线程数",
      desc: "线程越多越快，但会更占 CPU。",
      risk: "中",
      recommend: "按需",
      riskHint: "线程过高会导致卡顿。建议按 CPU 核数酌情设置。",
    },
    vad_threshold: {
      label: "人声检测阈值",
      desc: "越高越严格（更容易判静音）。",
      risk: "高",
      recommend: "按需",
      riskHint: "调高可能漏词；调低可能切得太碎。建议只在已启用人声检测且需要微调时调整。",
    },
    vad_min_dur: {
      label: "最短静音时长",
      desc: "越大越不容易切分（段更长）。",
      unit: "秒",
      risk: "中",
      recommend: "按需",
      riskHint: "过大可能把不该合并的句子合在一起。建议在切分过碎时小步调整。",
    },
    min_sub_duration: {
      label: "最短字幕时长",
      desc: "减少“闪字幕”，也降低配音对齐压力。",
      unit: "秒",
      risk: "中",
      recommend: "建议默认",
      riskHint: "会延长字幕尾部时间轴；极端情况可能影响对齐精细度。",
    },
    tts_split_len: {
      label: "配音拆分阈值",
      desc: "英文句子过长时先拆分再合成。",
      unit: "字符",
      risk: "中",
      recommend: "建议默认",
      riskHint: "值过小会更碎；值过大可能增加配音重复/卡顿风险。",
    },
    tts_speed_max: {
      label: "最大语速上限",
      desc: "控制配音最快能到多快。",
      unit: "倍",
      risk: "中",
      recommend: "建议默认",
      riskHint: "越大越快，容易出现明显加速。",
    },
    mux_slow_max_ratio: {
      label: "音画对齐：最大慢放比例",
      desc: "配音更长时视频可放慢的上限。",
      unit: "倍",
      risk: "中",
      recommend: "建议默认",
      riskHint: "越大画面越慢，过大显得拖沓。",
    },
    mux_slow_threshold_s: {
      label: "音画对齐：触发阈值",
      desc: "音频超时到这个值才慢放。",
      unit: "秒",
      risk: "中",
      recommend: "建议默认",
      riskHint: "值过小会频繁触发。",
    },
    // ---- lite/quality：句子单元、专名保护 ----
    sentence_unit_min_chars: { label: "句子单元：最小字数", unit: "字", risk: "中", recommend: "按需", desc: "过小会合并太多。", riskHint: "建议先开“句子单元”，再小步微调。" },
    sentence_unit_max_chars: { label: "句子单元：最大字数", unit: "字", risk: "中", recommend: "按需", desc: "过大会让段太长。", riskHint: "建议先开“句子单元”，再小步微调。" },
    sentence_unit_max_segs: { label: "句子单元：最多合并段数", unit: "段", risk: "中", recommend: "按需", desc: "控制合并幅度。", riskHint: "数值越大越可能改变时间轴结构。" },
    sentence_unit_max_gap_s: { label: "句子单元：最大跨段间隔", unit: "秒", risk: "中", recommend: "按需", desc: "间隔太大也会被合并。", riskHint: "字幕断句怪时可调；建议 0.1~0.8 小步试。" },
    entity_protect_min_len: {
      label: "专名保护：最短长度",
      unit: "字",
      risk: "中",
      recommend: "按需",
      desc: "过短会把普通词当专名。",
      riskHint: "误保护变多时调大。",
    },
    entity_protect_max_len: {
      label: "专名保护：最长长度",
      unit: "字",
      risk: "中",
      recommend: "按需",
      desc: "过长会把长句当专名。",
      riskHint: "保护范围过大时调小。",
    },
    entity_protect_min_freq: {
      label: "专名保护：最小出现次数",
      unit: "次",
      risk: "中",
      recommend: "按需",
      desc: "出现次数达到才会保护。",
      riskHint: "保护不足时可调小。",
    },
    entity_protect_max_items: {
      label: "专名保护：最多保护条目",
      unit: "条",
      risk: "中",
      recommend: "按需",
      desc: "限制保护数量，避免过多干预。",
      riskHint: "专名很多时可调大。",
    },

    // ---- lite：识别预处理 / 合并短段 / 纠错 ----
    asr_preprocess_highpass: { label: "识别预处理：高通滤波", unit: "Hz", risk: "中", recommend: "按需", desc: "去除低频隆隆声。", riskHint: "仅在开启“识别前音频预处理”后生效。" },
    asr_preprocess_lowpass: { label: "识别预处理：低通滤波", unit: "Hz", risk: "中", recommend: "按需", desc: "削弱高频噪声。", riskHint: "仅在开启“识别前音频预处理”后生效。" },
    asr_merge_min_dur_s: { label: "合并短段：最短段时长", unit: "秒", risk: "中", recommend: "按需" },
    asr_merge_min_chars: { label: "合并短段：最少字数", unit: "字", risk: "中", recommend: "按需" },
    asr_merge_max_gap_s: { label: "合并短段：最大间隔", unit: "秒", risk: "中", recommend: "按需" },
    asr_merge_max_group_chars: { label: "合并短段：最大合并字数", unit: "字", risk: "中", recommend: "按需" },
    asr_llm_fix_max_items: { label: "识别纠错：最多处理条目", unit: "条", risk: "高", recommend: "按需" },
    asr_llm_fix_min_chars: { label: "识别纠错：最小字数门槛", unit: "字", risk: "高", recommend: "按需" },

    // ---- quality：LLM / QE ----
    llm_chunk_size: { label: "LLM：每次处理段数", unit: "段", risk: "中", recommend: "建议默认", desc: "越小越稳定，越大越快。", riskHint: "值太大会增加服务崩溃概率。" },
    mt_context_window: { label: "翻译：上下文窗口", unit: "行", risk: "中", recommend: "建议默认", desc: "为每行翻译提供前/后一行上下文。", riskHint: "过大可能带来“串台”，一般 1~2 足够。" },
    mt_topic_auto_max_segs: {
      label: "自动主题：最多采样段数",
      unit: "段",
      risk: "中",
      recommend: "建议默认",
      desc: "采样越多越慢。",
      riskHint: "不稳定时调小。",
    },
    glossary_placeholder_max: {
      label: "术语占位符：最多条目",
      unit: "条",
      risk: "中",
      recommend: "按需",
      desc: "限制参与保护的术语数量。",
      riskHint: "术语很多时可调大。",
    },
    qe_threshold: { label: "质量评审：阈值", risk: "中", recommend: "建议默认", desc: "越低越严格（更多行会被判为可疑）。" },
    qe_max_items: { label: "质量评审：最多评审行数", unit: "行", risk: "中", recommend: "建议默认" },
    qe_time_budget_s: { label: "质量评审：时间预算", unit: "秒", risk: "中", recommend: "按需" },
    qe_embed_threshold: { label: "语义相似度：阈值", risk: "实验", recommend: "按需" },
    qe_embed_max_segs: { label: "语义相似度：最多分段数", unit: "段", risk: "实验", recommend: "按需" },
    qe_backtranslate_max_items: { label: "回译评审：最多评审行数", unit: "行", risk: "实验", recommend: "按需" },
    qe_backtranslate_overlap_threshold: { label: "回译评审：重叠阈值", risk: "实验", recommend: "按需" },
    max_sentence_len: { label: "句子最大长度", unit: "词/字", risk: "中", recommend: "建议默认", desc: "控制断句/处理上限。", riskHint: "过小可能切碎，过大可能变慢。" },

    // ---- quality：字幕后处理 / 显示字幕 ----
    subtitle_wrap_max_lines: { label: "字幕软换行：最多行数", unit: "行", risk: "中", recommend: "建议默认" },
    subtitle_cps_safety_gap: { label: "阅读速度补偿：安全间隔", unit: "秒", risk: "高", recommend: "建议默认" },
    display_max_chars_per_line: { label: "显示字幕：每行最多字符", unit: "字", risk: "中", recommend: "建议默认" },
    display_max_lines: { label: "显示字幕：最多行数", unit: "行", risk: "中", recommend: "建议默认" },
    display_merge_max_gap_s: { label: "显示字幕合并：最大间隔", unit: "秒", risk: "中", recommend: "建议默认" },
    display_merge_max_chars: { label: "显示字幕合并：最大字数", unit: "字", risk: "中", recommend: "建议默认" },
    display_split_max_chars: { label: "显示字幕拆分：最大字数", unit: "字", risk: "低", recommend: "建议默认" },

    // ---- quality：语义切句 ----
    meaning_split_min_chars: { label: "语义切句：最小触发字数", unit: "字", risk: "高", recommend: "按需" },
    meaning_split_max_parts: { label: "语义切句：最多拆分份数", unit: "份", risk: "高", recommend: "按需" },

    // ---- quality：硬字幕擦除 ----
    erase_subtitle_x: { label: "硬字幕区域：X（起点）", risk: "高", recommend: "按需", desc: "配合坐标模式使用。" },
    erase_subtitle_y: { label: "硬字幕区域：Y（起点）", risk: "高", recommend: "按需" },
    erase_subtitle_w: { label: "硬字幕区域：宽度", risk: "高", recommend: "按需" },
    erase_subtitle_h: { label: "硬字幕区域：高度", risk: "高", recommend: "按需" },
    erase_subtitle_blur_radius: { label: "硬字幕擦除：模糊半径", unit: "px", risk: "高", recommend: "按需" },

    // ---- quality：配音贴合/规划 ----
    tts_fit_wps: { label: "配音裁剪：目标语速", unit: "词/秒", risk: "高", recommend: "按需" },
    tts_fit_min_words: { label: "配音裁剪：最少词数", unit: "词", risk: "高", recommend: "按需" },
    tts_plan_safety_margin: { label: "语速规划：安全余量", risk: "高", recommend: "建议默认" },
    tts_plan_min_cap: { label: "语速规划：最低上限", unit: "倍", risk: "高", recommend: "建议默认" },

    // ---- quality：混音 ----
    bgm_gain_db: { label: "背景音音量", unit: "dB", risk: "中", recommend: "按需" },
    tts_gain_db: { label: "配音音量", unit: "dB", risk: "中", recommend: "按需" },
    bgm_sample_rate: { label: "混音采样率", unit: "Hz", risk: "中", recommend: "建议默认" },
  };

  const textMeta: Record<
    string,
    {
      label: string;
      desc?: string;
      risk?: RiskLevel;
      riskHint?: string;
      recommend?: "建议默认" | "按需";
      kind?: "text" | "password" | "select";
      placeholder?: string;
      options?: { label: string; value: string }[];
    }
  > = {
    // lite
    mt_model: { label: "翻译模型", desc: "离线翻译模型名称（HuggingFace）。", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：Helsinki-NLP/opus-mt-zh-en" },
    mt_device: { label: "翻译设备", desc: "auto 会自动选择（推荐）。", risk: "低", recommend: "建议默认", kind: "select", options: [{ label: "自动", value: "auto" }, { label: "CPU", value: "cpu" }, { label: "GPU", value: "cuda" }] },
    tts_backend: { label: "配音引擎", desc: "轻量模式默认使用 Piper（更易离线）。", risk: "中", recommend: "按需", kind: "select", options: [{ label: "Piper（离线）", value: "piper" }, { label: "Coqui（质量更好）", value: "coqui" }] },
    coqui_model: { label: "Coqui 模型", desc: "仅在配音引擎为 Coqui 时生效。", risk: "中", recommend: "按需", kind: "text" },
    coqui_device: { label: "Coqui 设备", desc: "auto 推荐。", risk: "低", recommend: "建议默认", kind: "select", options: [{ label: "自动", value: "auto" }, { label: "CPU", value: "cpu" }, { label: "GPU", value: "cuda" }] },

    // quality
    llm_endpoint: { label: "LLM 服务地址", desc: "本地/局域网 LLM 的 OpenAI 兼容地址。", risk: "高", recommend: "按需", kind: "text", placeholder: "例如：http://ollama:11434/v1" },
    llm_model: { label: "LLM 模型名", desc: "需与服务端已安装的模型一致。", risk: "高", recommend: "按需", kind: "text", placeholder: "例如：qwen2.5:7b" },
    llm_api_key: { label: "LLM 密钥", desc: "如服务需要鉴权则填写。", risk: "中", recommend: "按需", kind: "password" },
    mt_topic: { label: "翻译主题提示", desc: "为翻译提供固定主题（可空）。", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：科幻 / 法庭 / 医疗" },
    bgm_separate_method: { label: "背景音分离方式", desc: "none 表示不做分离（推荐默认）。", risk: "高", recommend: "按需", kind: "select", options: [{ label: "不分离（none）", value: "none" }, { label: "Demucs（更慢）", value: "demucs" }] },
    erase_subtitle_method: { label: "硬字幕擦除方法", desc: "默认 delogo。", risk: "高", recommend: "按需", kind: "select", options: [{ label: "delogo（推荐）", value: "delogo" }] },
    erase_subtitle_coord_mode: { label: "硬字幕坐标模式", desc: "ratio 为比例坐标。", risk: "高", recommend: "按需", kind: "select", options: [{ label: "比例（ratio）", value: "ratio" }, { label: "像素（px）", value: "px" }] },

    // online
    asr_endpoint: { label: "在线识别服务地址", risk: "高", recommend: "按需", kind: "text" },
    asr_api_key: { label: "在线识别密钥", risk: "高", recommend: "按需", kind: "password" },
    mt_endpoint: { label: "在线翻译服务地址", risk: "高", recommend: "按需", kind: "text" },
    mt_api_key: { label: "在线翻译密钥", risk: "高", recommend: "按需", kind: "password" },
    tts_endpoint: { label: "在线配音服务地址", risk: "高", recommend: "按需", kind: "text" },
    tts_api_key: { label: "在线配音密钥", risk: "高", recommend: "按需", kind: "password" },
    tts_voice: { label: "在线配音音色", risk: "中", recommend: "按需", kind: "text", placeholder: "例如：en-US-amy" },
  };

  const advanced = (
    <Content style={{ padding: 16 }}>
      <Card
        title="高级设置"
        extra={<Text type="secondary">按阶段整理配置（会随“当前模式”切换）</Text>}
      >
        {!config?.defaults ? (
          <Alert type="warning" showIcon message="配置尚未加载" description="请稍等，或点击右上角“重新检测”。" />
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message={`当前模式：${mode === "lite" ? "轻量" : mode === "quality" ? "质量" : "在线"}`}
              description="只展示当前模式可生效的配置。建议先保持默认，用到再改。"
              style={{ marginBottom: 12 }}
            />
            {mode === "quality" && (
              <Space align="center" style={{ marginBottom: 12 }}>
                <Text type="secondary">显示更多</Text>
                <Switch checked={advancedShowAll} onChange={setAdvancedShowAll} />
                {!advancedShowAll && <Text type="secondary">（常用项）</Text>}
              </Space>
            )}

              <Form form={form} layout="vertical">
              {(() => {
                const defaults = config.defaults || {};
                // 依据后端 TaskManager 实际 _build_cmd_* 会消费的字段，按模式过滤展示（避免“看得见但不生效”）
                const SUPPORT = {
                  lite: {
                    bool: new Set<string>([
                      "offline",
                      "vad_enable",
                      "denoise",
                      "bilingual_srt",
                      "lt_enable",
                      "skip_tts",
                      "asr_normalize_enable",
                      "asr_preprocess_enable",
                      "asr_preprocess_loudnorm",
                      "asr_merge_short_enable",
                      "asr_merge_save_debug",
                      "asr_llm_fix_enable",
                      "asr_llm_fix_save_debug",
                      "sentence_unit_enable",
                      "entity_protect_enable",
                    ]),
                    num: new Set<string>([
                      "sample_rate",
                      "whispercpp_threads",
                      "vad_threshold",
                      "vad_min_dur",
                      "min_sub_duration",
                      "tts_split_len",
                      "tts_speed_max",
                      "mux_slow_max_ratio",
                      "mux_slow_threshold_s",
                      "asr_preprocess_highpass",
                      "asr_preprocess_lowpass",
                      "asr_merge_min_dur_s",
                      "asr_merge_min_chars",
                      "asr_merge_max_gap_s",
                      "asr_merge_max_group_chars",
                      "asr_llm_fix_max_items",
                      "asr_llm_fix_min_chars",
                      "sentence_unit_min_chars",
                      "sentence_unit_max_chars",
                      "sentence_unit_max_segs",
                      "sentence_unit_max_gap_s",
                      "entity_protect_min_len",
                      "entity_protect_max_len",
                      "entity_protect_min_freq",
                      "entity_protect_max_items",
                    ]),
                    str: new Set<string>(["mt_model", "mt_device", "tts_backend", "coqui_model", "coqui_device"]),
                  },
                  quality: {
                    bool: new Set<string>([
                      "offline",
                      "vad_enable",
                      "denoise",
                      "bilingual_srt",
                      "sentence_unit_enable",
                      "entity_protect_enable",
                      "glossary_prompt_enable",
                      "mt_json_enable",
                      "mt_topic_auto_enable",
                      "glossary_placeholder_enable",
                      "qe_enable",
                      "qe_save_report",
                      "qe_embed_enable",
                      "qe_backtranslate_enable",
                      "diarization",
                      "asr_normalize_enable",
                      "subtitle_postprocess_enable",
                      "subtitle_wrap_enable",
                      "subtitle_cps_fix_enable",
                      "tts_script_enable",
                      "tts_script_strict_clean_enable",
                      "display_srt_enable",
                      "display_use_for_embed",
                      "display_merge_enable",
                      "display_split_enable",
                      "mt_pause_before_translate",
                      "meaning_split_enable",
                      "meaning_split_save_debug",
                      "erase_subtitle_enable",
                      "tts_fit_enable",
                      "tts_fit_save_raw",
                      "tts_plan_enable",
                      "bgm_mix_enable",
                      "bgm_duck_enable",
                      "bgm_loudnorm_enable",
                      "tra_enable",
                      "tra_save_debug",
                      "tra_json_enable",
                      "tra_auto_enable",
                    ]),
                    num: new Set<string>([
                      "sample_rate",
                      "max_sentence_len",
                      "min_sub_duration",
                      "tts_split_len",
                      "tts_speed_max",
                      "mux_slow_max_ratio",
                      "mux_slow_threshold_s",
                      "mt_context_window",
                      "llm_chunk_size",
                      "mt_topic_auto_max_segs",
                      "glossary_placeholder_max",
                      "qe_threshold",
                      "qe_max_items",
                      "qe_time_budget_s",
                      "qe_embed_threshold",
                      "qe_embed_max_segs",
                      "qe_backtranslate_max_items",
                      "qe_backtranslate_overlap_threshold",
                      "vad_threshold",
                      "vad_min_dur",
                      "sentence_unit_min_chars",
                      "sentence_unit_max_chars",
                      "sentence_unit_max_segs",
                      "sentence_unit_max_gap_s",
                      "entity_protect_min_len",
                      "entity_protect_max_len",
                      "entity_protect_min_freq",
                      "entity_protect_max_items",
                      "subtitle_wrap_max_lines",
                      "subtitle_cps_safety_gap",
                      "display_max_chars_per_line",
                      "display_max_lines",
                      "display_merge_max_gap_s",
                      "display_merge_max_chars",
                      "display_split_max_chars",
                      "meaning_split_min_chars",
                      "meaning_split_max_parts",
                      "erase_subtitle_x",
                      "erase_subtitle_y",
                      "erase_subtitle_w",
                      "erase_subtitle_h",
                      "erase_subtitle_blur_radius",
                      "tts_fit_wps",
                      "tts_fit_min_words",
                      "tts_plan_safety_margin",
                      "tts_plan_min_cap",
                      "bgm_gain_db",
                      "tts_gain_db",
                      "bgm_sample_rate",
                    ]),
                    str: new Set<string>([
                      "llm_endpoint",
                      "llm_model",
                      "llm_api_key",
                      "mt_topic",
                      "coqui_model",
                      "coqui_device",
                      "bgm_separate_method",
                      "erase_subtitle_method",
                      "erase_subtitle_coord_mode",
                    ]),
                  },
                  online: {
                    bool: new Set<string>([]),
                    num: new Set<string>(["sample_rate", "min_sub_duration", "tts_split_len", "tts_speed_max"]),
                    str: new Set<string>([
                      "asr_endpoint",
                      "asr_api_key",
                      "mt_endpoint",
                      "mt_api_key",
                      "mt_model",
                      "tts_endpoint",
                      "tts_api_key",
                      "tts_voice",
                    ]),
                  },
                } as const;

                const support = mode === "quality" ? SUPPORT.quality : mode === "online" ? SUPPORT.online : SUPPORT.lite;

                const DEV_ONLY_KEYS = new Set<string>([
                  // Debug（只写调试文件/报告）
                  "asr_merge_save_debug",
                  "asr_llm_fix_save_debug",
                  "meaning_split_save_debug",
                  "tts_fit_save_raw",
                  "tra_save_debug",
                  "qe_save_report",
                  // 工作流/辅助/不完整交付
                  "mt_skip_if_present",
                  "mt_pause_before_translate",
                  "skip_tts",
                  // 占位/未实现/审计项/字典驱动
                  "diarization",
                  "qe_backtranslate_enable",
                  "glossary_placeholder_enable",
                  "glossary_prompt_enable",
                  // 占位/不生效（见 docs/轻量模式配置项总表.md）
                  "dedupe",
                  // 质量模式：明确不推荐默认开启或已验证负收益
                  "tts_script_enable",
                  "mt_json_enable",
                  "mt_topic_auto_enable",
                  "display_merge_enable",
                  "bgm_mix_enable",
                  "bgm_duck_enable",
                  "bgm_loudnorm_enable",
                  "qe_backtranslate_enable",
                  // 待补测 / 子开关：先不对普通用户展示
                  "display_srt_enable",
                  "subtitle_wrap_enable",
                  "subtitle_cps_fix_enable",
                  "display_max_chars_per_line",
                  "display_max_lines",
                  "display_merge_max_gap_s",
                  "display_merge_max_chars",
                  "display_split_max_chars",
                  "mt_topic_auto_max_segs",
                  "tts_gain_db",
                  "bgm_gain_db",
                  // 密钥/服务相关：仅开发者
                  "asr_endpoint",
                  "asr_api_key",
                  "mt_endpoint",
                  "mt_api_key",
                  "tts_endpoint",
                  "tts_api_key",
                  "tts_voice",
                  "llm_endpoint",
                  "llm_model",
                  "llm_api_key",
                ]);
                const HIDDEN_KEYS = new Set<string>([
                  // 硬字幕擦除只由业务流程控制，不在高级设置展示
                  "erase_subtitle_enable",
                  "erase_subtitle_method",
                  "erase_subtitle_coord_mode",
                  "erase_subtitle_x",
                  "erase_subtitle_y",
                  "erase_subtitle_w",
                  "erase_subtitle_h",
                  "erase_subtitle_blur_radius",
                  // 翻译主题提示移入 Step2
                  "mt_topic",
                ]);

                const rawBoolKeys = Object.keys(defaults)
                  .filter((k) => typeof (defaults as any)[k] === "boolean")
                  .filter((k) => support.bool.has(k))
                  .filter((k) => !HIDDEN_KEYS.has(k));
                const rawNumKeys = Object.keys(defaults)
                  .filter((k) => typeof (defaults as any)[k] === "number")
                  .filter((k) => support.num.has(k))
                  .filter((k) => !HIDDEN_KEYS.has(k));
                const rawStrKeys = Object.keys(defaults)
                  .filter((k) => typeof (defaults as any)[k] === "string")
                  .filter((k) => support.str.has(k))
                  .filter((k) => !HIDDEN_KEYS.has(k));

                const boolUserKeys = rawBoolKeys.filter((k) => !DEV_ONLY_KEYS.has(k) && !!toggleMeta[k]);
                const boolDevKeys = rawBoolKeys.filter((k) => DEV_ONLY_KEYS.has(k) || !toggleMeta[k]);

                const numUserKeys = rawNumKeys.filter((k) => !!paramMeta[k] && !DEV_ONLY_KEYS.has(k));
                const numDevKeys = rawNumKeys.filter((k) => !paramMeta[k] || DEV_ONLY_KEYS.has(k));

                const strUserKeys = rawStrKeys.filter((k) => !!textMeta[k] && !DEV_ONLY_KEYS.has(k));
                const strDevKeys = rawStrKeys.filter((k) => !textMeta[k] || DEV_ONLY_KEYS.has(k));

                const byStageBool: Record<string, string[]> = {};
                for (const k of boolUserKeys) {
                  const stage = stageOfToggle(k);
                  (byStageBool[stage] ||= []).push(k);
                }
                const byStageNum: Record<string, string[]> = {};
                for (const k of numUserKeys) {
                  const stage = stageOfToggle(k);
                  (byStageNum[stage] ||= []).push(k);
                }
                const byStageStr: Record<string, string[]> = {};
                for (const k of strUserKeys) {
                  const stage = stageOfToggle(k);
                  (byStageStr[stage] ||= []).push(k);
                }

                const COMMON_QUALITY_BOOL = new Set<string>([
                  "subtitle_postprocess_enable",
                  "meaning_split_enable",
                  "tts_plan_enable",
                  "tts_fit_enable",
                  "bilingual_srt",
                  "entity_protect_enable",
                ]);
                const COMMON_QUALITY_NUM = new Set<string>([
                  "tts_speed_max",
                  "mux_slow_max_ratio",
                  "mux_slow_threshold_s",
                ]);
                const COMMON_QUALITY_STR = new Set<string>([]);
                const showAll = mode === "quality" ? advancedShowAll : true;
                const inCommon = (k: string) =>
                  mode === "quality"
                    ? COMMON_QUALITY_BOOL.has(k) || COMMON_QUALITY_NUM.has(k) || COMMON_QUALITY_STR.has(k)
                    : true;
                const filterCommon = (list: string[]) => (showAll ? list : list.filter(inCommon));

                const stageOrder = ["语音识别", "翻译", "配音", "合成"];
                const stageLabel: Record<string, string> = {
                  语音识别: "语音识别",
                  翻译: "翻译",
                  配音: "配音",
                  合成: "合成",
                  常用: "常用",
                  "开发者（仅用于排查）": "开发者",
                };
                const tabs =
                  mode === "quality" && !showAll
                    ? ["常用"]
                    : stageOrder.filter(
                        (s) =>
                          filterCommon(byStageBool[s] || []).length > 0 ||
                          filterCommon(byStageNum[s] || []).length > 0 ||
                          filterCommon(byStageStr[s] || []).length > 0
                      );
                if (devToolsEnabled && showAll) tabs.push("开发者（仅用于排查）");

                const DEPENDS_ON: Record<string, string> = {
                  // 子开关依赖总开关
                  subtitle_wrap_enable: "subtitle_postprocess_enable",
                  subtitle_cps_fix_enable: "subtitle_postprocess_enable",
                  display_use_for_embed: "display_srt_enable",
                  display_merge_enable: "display_srt_enable",
                  display_split_enable: "display_srt_enable",
                  bgm_duck_enable: "bgm_mix_enable",
                  bgm_loudnorm_enable: "bgm_mix_enable",
                  qe_embed_enable: "qe_enable",
                  tra_json_enable: "tra_enable",
                  tra_auto_enable: "tra_enable",
                  tts_script_strict_clean_enable: "tts_script_enable",
                  tts_fit_save_raw: "tts_fit_enable",
                  asr_preprocess_loudnorm: "asr_preprocess_enable",
                  asr_merge_save_debug: "asr_merge_short_enable",
                  asr_llm_fix_save_debug: "asr_llm_fix_enable",
                  meaning_split_save_debug: "meaning_split_enable",
                };

                const CHILDREN: Record<string, string[]> = {
                  subtitle_postprocess_enable: ["subtitle_wrap_enable", "subtitle_cps_fix_enable"],
                  display_srt_enable: ["display_use_for_embed", "display_merge_enable", "display_split_enable"],
                  bgm_mix_enable: ["bgm_duck_enable", "bgm_loudnorm_enable"],
                  qe_enable: ["qe_embed_enable", "qe_backtranslate_enable"],
                  tra_enable: ["tra_json_enable", "tra_auto_enable"],
                  tts_script_enable: ["tts_script_strict_clean_enable"],
                  tts_fit_enable: ["tts_fit_save_raw"],
                  asr_preprocess_enable: ["asr_preprocess_loudnorm"],
                  asr_merge_short_enable: ["asr_merge_save_debug"],
                  asr_llm_fix_enable: ["asr_llm_fix_save_debug"],
                  meaning_split_enable: ["meaning_split_save_debug"],
                };

                function dependencyHint(k: string, getFieldValue: any): string {
                  const parent = DEPENDS_ON[k];
                  if (!parent) return "";
                  const parentOn = !!getFieldValue(parent);
                  if (parentOn) return "";
                  const parentLabel = toggleMeta[parent]?.label || "前置开关";
                  return `需要先开启「${parentLabel}」`;
                }

                function renderBoolCard(k: string) {
                  const meta = toggleMeta[k] || { label: "未命名配置项", desc: "该项尚未补齐中文说明。建议保持默认。", risk: "未知" as const };
                  const risk = (meta as any).risk as RiskLevel | undefined;
                  const recommend = (meta as any).recommend as string | undefined;
                  const riskHint = (meta as any).riskHint as string | undefined;
                  return (
                    <Form.Item key={k} noStyle shouldUpdate={(prev, cur) => prev?.[k] !== cur?.[k]}>
                      {({ getFieldValue, setFieldsValue }) => {
                        const hint = dependencyHint(k, getFieldValue);
                        const disabled = !!hint;
                        return (
                          <Card size="small">
                            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                              <Space direction="vertical" size={6} style={{ maxWidth: 620 }}>
                                <Space align="center" wrap>
                                  <Text strong>{meta.label}</Text>
                                  <Tooltip
                                    title={
                                      <div style={{ maxWidth: 360 }}>
                                        <div><b>提示：</b>{meta.desc || "按需调整"}</div>
                                        {recommend && <div><b>建议：</b>{recommend}</div>}
                                        {riskHint && <div style={{ marginTop: 6 }}>{riskHint}</div>}
                                        {hint && <div style={{ marginTop: 6 }}><b>联动：</b>{hint}</div>}
                                      </div>
                                    }
                                  >
                                    <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
                                  </Tooltip>
                                </Space>
                              </Space>
                              <Form.Item name={k} valuePropName="checked" style={{ margin: 0 }}>
                                <Switch
                                  checkedChildren="开启"
                                  unCheckedChildren="关闭"
                                  disabled={disabled}
                                  onChange={(checked) => {
                                    if (!checked) {
                                      const children = CHILDREN[k] || [];
                                      if (children.length > 0) {
                                        const patch: Record<string, any> = {};
                                        for (const c of children) patch[c] = false;
                                        setFieldsValue(patch);
                                      }
                                    }
                                  }}
                  />
                  </Form.Item>
                            </Space>
                          </Card>
                        );
                      }}
                  </Form.Item>
                  );
                }

                const DEPENDS_ON_PARAM: Record<string, string> = {
                  vad_threshold: "vad_enable",
                  vad_min_dur: "vad_enable",
                  entity_protect_min_len: "entity_protect_enable",
                  entity_protect_max_len: "entity_protect_enable",
                  entity_protect_min_freq: "entity_protect_enable",
                  entity_protect_max_items: "entity_protect_enable",
                  glossary_placeholder_max: "glossary_placeholder_enable",
                  mt_topic_auto_max_segs: "mt_topic_auto_enable",
                  display_max_chars_per_line: "display_srt_enable",
                  display_max_lines: "display_srt_enable",
                  display_merge_max_gap_s: "display_srt_enable",
                  display_merge_max_chars: "display_srt_enable",
                  display_split_max_chars: "display_srt_enable",
                  tts_fit_wps: "tts_fit_enable",
                  tts_fit_min_words: "tts_fit_enable",
                };

                function renderTextCard(k: string) {
                  const meta = textMeta[k];
                  return (
                    <Form.Item key={k} noStyle shouldUpdate={(prev, cur) => prev?.[k] !== cur?.[k]}>
                      {({ getFieldValue }) => {
                        const input =
                          meta.kind === "password" ? (
                            <Input.Password style={{ width: 260 }} placeholder={meta.placeholder || "保持默认"} />
                          ) : meta.kind === "select" ? (
                            <Select style={{ width: 260 }} options={meta.options || []} placeholder={meta.placeholder || "保持默认"} />
                          ) : (
                            <Input style={{ width: 260 }} placeholder={meta.placeholder || "保持默认"} />
                          );
                        return (
                          <Card size="small">
                            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                              <Space direction="vertical" size={6} style={{ maxWidth: 620 }}>
                                <Space align="center" wrap>
                                  <Text strong>{meta.label}</Text>
                                  <Tooltip
                                    title={
                                      <div style={{ maxWidth: 360 }}>
                                        <div><b>提示：</b>{meta.desc || "按需配置"}</div>
                                        {meta.recommend && <div><b>建议：</b>{meta.recommend}</div>}
                                        {meta.riskHint && <div style={{ marginTop: 6 }}>{meta.riskHint}</div>}
                                      </div>
                                    }
                                  >
                                    <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
                                  </Tooltip>
                                </Space>
                              </Space>
                              <Form.Item name={k} style={{ margin: 0 }}>
                                {input}
                  </Form.Item>
                </Space>
                          </Card>
                        );
                      }}
                  </Form.Item>
                  );
                }

                function renderNumberCard(k: string) {
                  const meta = paramMeta[k];
                  return (
                    <Form.Item key={k} noStyle shouldUpdate={(prev, cur) => prev?.[k] !== cur?.[k]}>
                      {({ getFieldValue }) => {
                        const parent = DEPENDS_ON_PARAM[k];
                        const parentOn = parent ? !!getFieldValue(parent) : true;
                        const disabled = !parentOn;
                        const hint = !parentOn ? `需要先开启「${toggleMeta[parent]?.label || "前置开关"}」` : "";
                        const step =
                          k === "vad_threshold" || k === "vad_min_dur" || k === "min_sub_duration" || k === "tts_speed_max" ? 0.1 : 1;
                        const min = k === "sample_rate" ? 8000 : k === "vad_threshold" ? 0 : 0;
                        const max = k === "sample_rate" ? 48000 : k === "vad_threshold" ? 1 : undefined;
                        return (
                          <Card size="small">
                            <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                              <Space direction="vertical" size={6} style={{ maxWidth: 620 }}>
                                <Space align="center" wrap>
                                  <Text strong>{meta.label}</Text>
                                  <Tooltip
                                    title={
                                      <div style={{ maxWidth: 360 }}>
                                        <div><b>提示：</b>{meta.desc || "按需调整"}</div>
                                        {meta.recommend && <div><b>建议：</b>{meta.recommend}</div>}
                                        {meta.riskHint && <div style={{ marginTop: 6 }}>{meta.riskHint}</div>}
                                        {hint && <div style={{ marginTop: 6 }}><b>联动：</b>{hint}</div>}
                                      </div>
                                    }
                                  >
                                    <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
                                  </Tooltip>
                                </Space>
                              </Space>
                              <Form.Item name={k} style={{ margin: 0 }}>
                                <InputNumber style={{ width: 160 }} step={step} min={min} max={max} placeholder="保持默认" disabled={disabled} />
                  </Form.Item>
                            </Space>
                          </Card>
                        );
                      }}
                  </Form.Item>
                  );
                }

                const commonBool = boolUserKeys.filter((k) => COMMON_QUALITY_BOOL.has(k));
                const commonNum = numUserKeys.filter((k) => COMMON_QUALITY_NUM.has(k));
                const commonStr = strUserKeys.filter((k) => COMMON_QUALITY_STR.has(k));

                const orderByDependency = (list: string[]) => {
                  const set = new Set(list);
                  const out: string[] = [];
                  for (const k of list) {
                    if (!set.has(k)) continue;
                    out.push(k);
                    const children = CHILDREN[k] || [];
                    for (const c of children) {
                      if (set.has(c)) out.push(c);
                    }
                    set.delete(k);
                    for (const c of children) set.delete(c);
                  }
                  return out;
                };

                return (
                  <Tabs
                    defaultActiveKey={tabs[0]}
                    items={tabs.map((stage) => ({
                      key: stage,
                      label: stageLabel[stage] || stage,
                      children: (
                        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                          {stage === "常用" && (
                            <>
                              {commonStr.length > 0 && (
                                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                  {commonStr.map((k) => renderTextCard(k))}
                                </Space>
                              )}
                              {commonNum.length > 0 && (
                                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                  {commonNum.map((k) => renderNumberCard(k))}
                                </Space>
                              )}
                              {commonBool.length > 0 && (
                                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                  {orderByDependency(commonBool).map((k) => renderBoolCard(k))}
                                </Space>
                              )}
                            </>
                          )}
                          {stage === "字幕与成片" && (
                            <Alert
                              type="info"
                              showIcon
                              message="音画对齐策略已固定为整体慢放"
                              description="末尾定格已下线；这里只保留慢放比例与触发阈值可调。"
                            />
                          )}
                          {stage !== "开发者（仅用于排查）" && stage !== "常用" && filterCommon(byStageStr[stage] || []).length > 0 && (
                            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                              {filterCommon(byStageStr[stage] || []).map((k) => renderTextCard(k))}
                            </Space>
                          )}
                          {stage !== "开发者（仅用于排查）" && stage !== "常用" && filterCommon(byStageNum[stage] || []).length > 0 && (
                            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                              {filterCommon(byStageNum[stage] || []).map((k) => renderNumberCard(k))}
                            </Space>
                          )}

                          {stage !== "开发者（仅用于排查）" && stage !== "常用" && filterCommon(byStageBool[stage] || []).length > 0 && (
                            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                              {orderByDependency(filterCommon(byStageBool[stage] || [])).map((k) => renderBoolCard(k))}
                            </Space>
                          )}

                          {stage === "开发者（仅用于排查）" && devToolsEnabled && (
                            <>
                              <Alert
                                type="warning"
                                showIcon
                                message="开发者项：仅用于排查与评测"
                                description="这些项更容易造成回归、或需要工程理解；不要在普通交付中随意更改。"
                              />
                              {(() => {
                                const devStageOrder = ["语音识别", "翻译", "配音", "合成", "开发者"];
                                const byStageDevBool: Record<string, string[]> = {};
                                for (const k of boolDevKeys) {
                                  const stage = stageOfToggle(k);
                                  (byStageDevBool[stage] ||= []).push(k);
                                }
                                const byStageDevNum: Record<string, string[]> = {};
                                for (const k of numDevKeys) {
                                  const stage = stageOfToggle(k);
                                  (byStageDevNum[stage] ||= []).push(k);
                                }
                                const byStageDevStr: Record<string, string[]> = {};
                                for (const k of strDevKeys) {
                                  const stage = stageOfToggle(k);
                                  (byStageDevStr[stage] ||= []).push(k);
                                }
                                return devStageOrder.map((stage) => {
                                  const b = byStageDevBool[stage] || [];
                                  const n = byStageDevNum[stage] || [];
                                  const s = byStageDevStr[stage] || [];
                                  if (b.length + n.length + s.length === 0) return null;
                                  return (
                                    <Card key={stage} size="small" title={stage}>
                                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                        {s.map((k) => renderTextCard(k))}
                                        {n.map((k) => (
                                          <Form.Item key={k} noStyle shouldUpdate={(p, c) => p?.[k] !== c?.[k]}>
                                            {({ getFieldValue }) => {
                                              const currentVal = getFieldValue(k);
                                              const defaultVal = (defaults as any)[k];
                                              return (
                                                <Card size="small">
                                                  <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                                                    <Space direction="vertical" size={4} style={{ maxWidth: 620 }}>
                                                      <Space wrap>
                                                        <Text strong>{paramMeta[k]?.label || k}</Text>
                                                        <Tooltip title="开发者项：用于排查/评测。">
                                                          <Button size="small" type="text" icon={<QuestionCircleOutlined />} />
                                                        </Tooltip>
                                                      </Space>
                                                      <Space wrap>
                                                        <Tag color={currentVal !== undefined && currentVal !== null ? "blue" : "default"}>当前：{String(currentVal ?? "未设置")}</Tag>
                                                        <Tag>默认：{String(defaultVal)}</Tag>
                                                        <Tag>内部键：{k}</Tag>
                                                      </Space>
                                                    </Space>
                                                    <Form.Item name={k} style={{ margin: 0 }}>
                                                      <InputNumber style={{ width: 160 }} placeholder="保持默认" />
                                                    </Form.Item>
                                                  </Space>
                                                </Card>
                                              );
                                            }}
                                          </Form.Item>
                                        ))}
                                        {orderByDependency(b).map((k) => renderBoolCard(k))}
                                      </Space>
                                    </Card>
                                  );
                                });
                              })()}
                            </>
                          )}

                <Space>
                            <Button
                              type="primary"
                              onClick={() => {
                                const values = form.getFieldsValue(true) || {};
                                const toggles: Record<string, boolean> = {};
                                const params: Record<string, number | string> = {};
                                for (const k of Object.keys(defaults)) {
                                  if (typeof (defaults as any)[k] === "boolean" && typeof values[k] === "boolean") toggles[k] = values[k];
                                  if (typeof (defaults as any)[k] === "number" && typeof values[k] === "number") {
                                    if (paramMeta[k]) params[k] = values[k];
                                  }
                                  if (typeof (defaults as any)[k] === "string" && typeof values[k] === "string") {
                                    if (textMeta[k]) params[k] = values[k];
                                  }
                                }
                                const next = { ...uiPrefs, defaultToggles: toggles, defaultParams: params };
                                setUiPrefs(next);
                                saveUiPrefs(next);
                                message.success("已保存为默认高级设置");
                              }}
                            >
                              保存为默认
                  </Button>
                            <Button
                              onClick={() => {
                                const patch: Record<string, any> = {};
                                for (const k of Object.keys(defaults)) {
                                  if (typeof (defaults as any)[k] === "boolean") patch[k] = (defaults as any)[k];
                                  if (typeof (defaults as any)[k] === "number" && paramMeta[k]) patch[k] = (defaults as any)[k];
                                  if (typeof (defaults as any)[k] === "string" && textMeta[k]) patch[k] = (defaults as any)[k];
                                }
                                form.setFieldsValue(patch);
                                message.info("已恢复为后端默认配置");
                              }}
                            >
                              恢复默认
                  </Button>
                </Space>
                        </Space>
                      ),
                    }))}
                  />
                );
              })()}
              </Form>
          </>
        )}
            </Card>
    </Content>
  );

  function tierLabel(t: string | undefined | null): string {
    if (!t) return "-";
    if (t === "normal") return "普通";
    if (t === "mid") return "中端";
    if (t === "high") return "高端";
    return String(t);
  }

  const system = (
    <Content style={{ padding: 16 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card title="系统状态" extra={<Text type="secondary">用于确认后端与硬件信息</Text>}>
          <Space align="center" wrap style={{ width: "100%", justifyContent: "space-between" }}>
                <Space align="center" wrap>
              <Tag color={health === "ok" ? "green" : "red"}>{health === "ok" ? "后端可用" : "后端不可用"}</Tag>
              <Text type="secondary">后端地址：{apiBase}</Text>
              {hardware && <Tag color="blue">硬件档位：{tierLabel(hardware.tier)}</Tag>}
                </Space>
            <Button icon={<ReloadOutlined />} loading={loadingBoot} onClick={bootstrap}>
              重新检测
            </Button>
          </Space>
          {hardware && (
            <>
              <Divider />
              <Descriptions size="small" column={3}>
                <Descriptions.Item label="CPU 线程">{hardware.cpu_cores ?? "-"}</Descriptions.Item>
                <Descriptions.Item label="内存(GB)">{hardware.memory_gb ?? "-"}</Descriptions.Item>
                <Descriptions.Item label="GPU">{hardware.gpu_name || "无"}</Descriptions.Item>
                <Descriptions.Item label="显存(GB)">{hardware.gpu_vram_gb ?? "-"}</Descriptions.Item>
              </Descriptions>
            </>
          )}
        </Card>

        <Card title="系统" extra={<Text type="secondary">默认设置会在下次打开时自动生效</Text>}>
          <Form layout="vertical">
            <Form.Item label="默认预设（随模式变化）" extra="不同模式支持的预设不同；这里会自动过滤为当前模式可用的预设。">
              <Select
                value={uiPrefs.defaultPreset || preset}
                onChange={(v) => setUiPrefs((p) => ({ ...p, defaultPreset: v }))}
                options={allowedPresetOptions.map((p) => ({ value: p.key, label: p.label }))}
              />
            </Form.Item>
            <Divider />
            <Form.Item label="开发者选项" extra="用于测试：开启后会显示“开发者类目/内部键/更多不建议对外暴露的项”。">
              <Switch
                checked={devToolsEnabled}
                checkedChildren="开启"
                unCheckedChildren="关闭"
                onChange={(checked) => {
                  const next = { ...uiPrefs, devToolsEnabled: checked };
                  setUiPrefs(next);
                  saveUiPrefs(next);
                  message.success(checked ? "已开启开发者选项" : "已关闭开发者选项");
                }}
              />
            </Form.Item>
            <Divider />
            <Space>
              <Button
                type="primary"
                onClick={() => {
                  saveUiPrefs(uiPrefs);
                  message.success("已保存系统设置");
                  if (uiPrefs.defaultMode) setMode(uiPrefs.defaultMode);
                  if (uiPrefs.defaultPreset) setPreset(uiPrefs.defaultPreset);
                  const toggles = uiPrefs.defaultToggles || {};
                  for (const [k, v] of Object.entries(toggles)) {
                    if (typeof v === "boolean") form.setFieldsValue({ [k]: v });
                  }
                  const params = uiPrefs.defaultParams || {};
                  for (const [k, v] of Object.entries(params)) {
                    if (typeof v === "number" || typeof v === "string") form.setFieldsValue({ [k]: v });
                  }
                }}
              >
                保存
              </Button>
              <Button
                onClick={() => {
                  const fresh = loadUiPrefs();
                  setUiPrefs(fresh);
                  message.info("已重新载入系统设置");
                }}
              >
                重新载入
                  </Button>
                </Space>
              </Form>
            </Card>
      </Space>
    </Content>
  );

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={220}
        theme="light"
        style={{ borderRight: "1px solid #f0f0f0" }}
        collapsible
        collapsed={siderCollapsed}
        trigger={null}
        onCollapse={(v) => setSiderCollapsed(v)}
      >
        <div style={{ padding: "16px 16px 8px 16px" }}>
          <Space align="center" style={{ width: "100%", justifyContent: "space-between" }}>
            {!siderCollapsed ? (
              <Title level={5} style={{ margin: 0 }}>
                <Space size={6}>
                  <RocketOutlined />
                  译制工坊
                </Space>
              </Title>
            ) : (
              <Title level={5} style={{ margin: 0 }}>
                <RocketOutlined />
              </Title>
            )}
            <Button
              size="small"
              type="text"
              icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setSiderCollapsed((v) => !v)}
            />
          </Space>
          {!siderCollapsed && <Text type="secondary"></Text>}
                </div>
        <Menu
          mode="inline"
          selectedKeys={[route]}
          onClick={(e) => setRoute(e.key as any)}
          items={[
            { key: "mode", icon: <ThunderboltOutlined />, label: "模式选择" },
            { key: "wizard", icon: <PlusOutlined />, label: "新建批次" },
            { key: "workbench", icon: <AppstoreOutlined />, label: "任务中心" },
            { key: "history", icon: <HistoryOutlined />, label: "历史记录" },
            { key: "advanced", icon: <SettingOutlined />, label: "高级设置" },
            { key: "system", icon: <SettingOutlined />, label: "系统" },
          ]}
        />
      </Sider>
      <Layout>
        {route === "wizard"
          ? wizard
          : route === "workbench"
            ? workbench
            : route === "history"
              ? history
              : route === "mode"
                ? modeSelect
                : route === "advanced"
                  ? advanced
                  : system}
      </Layout>

      <input
        ref={regionPickerFileInputRef}
        type="file"
        accept="video/*"
        style={{ display: "none" }}
        onChange={handleRegionPickerFileChange}
      />

      <Modal
        title="术语表（用于翻译前固定表达）"
        open={glossaryModalOpen}
        onCancel={() => setGlossaryModalOpen(false)}
        footer={
          <Space wrap>
            <Button onClick={openGlossaryModal} disabled={glossaryLoading}>
              重新加载
            </Button>
            <Button
              onClick={() => downloadTextFile("glossary.json", JSON.stringify(glossaryDocFromRows(glossaryItems), null, 2))}
              disabled={glossaryLoading}
            >
              下载术语表
            </Button>
            <Button
              onClick={() =>
                setGlossaryItems([
                  {
                    id: createId(),
                    src: "产品名/人名/地名（中文）",
                    tgt: "English Name",
                    aliases: "别名1, 别名2",
                    forbidden: "错误译法1",
                    note: "可选备注",
                  },
                ])
              }
              disabled={glossaryLoading}
            >
              填入示例
            </Button>
            <Button type="primary" onClick={saveGlossary} loading={glossaryLoading}>
              保存术语表
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="用法：填写“中文 → 英文”，可选别名与禁止译法。保存后对新建批次生效。"
          />
          {glossaryError && <Alert type="warning" showIcon message={glossaryError} />}
          <Text type="secondary">提示：只要填“中文/英文”即可生效，其它可不填。</Text>
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={glossaryItems}
            columns={[
              {
                title: "中文",
                dataIndex: "src",
                render: (v: string, r: any) => (
                  <Input value={v} placeholder="中文术语" onChange={(e) => updateGlossaryRow(r.id, { src: e.target.value })} />
                ),
              },
              {
                title: "英文",
                dataIndex: "tgt",
                render: (v: string, r: any) => (
                  <Input value={v} placeholder="英文译法" onChange={(e) => updateGlossaryRow(r.id, { tgt: e.target.value })} />
                ),
              },
              {
                title: "别名（可选）",
                dataIndex: "aliases",
                render: (v: string, r: any) => (
                  <Input value={v} placeholder="用逗号分隔" onChange={(e) => updateGlossaryRow(r.id, { aliases: e.target.value })} />
                ),
              },
              {
                title: "禁止译法（可选）",
                dataIndex: "forbidden",
                render: (v: string, r: any) => (
                  <Input value={v} placeholder="用逗号分隔" onChange={(e) => updateGlossaryRow(r.id, { forbidden: e.target.value })} />
                ),
              },
              {
                title: "备注（可选）",
                dataIndex: "note",
                render: (v: string, r: any) => (
                  <Input value={v} placeholder="备注" onChange={(e) => updateGlossaryRow(r.id, { note: e.target.value })} />
                ),
              },
              {
                title: "操作",
                dataIndex: "op",
                width: 70,
                render: (_: any, r: any) => (
                  <Button size="small" danger onClick={() => removeGlossaryRow(r.id)}>
                    删除
                  </Button>
                ),
              },
            ]}
          />
          <Button onClick={addGlossaryRow}>添加术语</Button>
        </Space>
      </Modal>

      <Modal
        title="翻译主题（可选）"
        open={mtTopicModalOpen}
        onCancel={() => setMtTopicModalOpen(false)}
        onOk={() => {
          form.setFieldsValue({ mt_topic: mtTopicDraft.trim() });
          setMtTopicModalOpen(false);
          message.success("已设置翻译主题");
        }}
        okText="应用"
        cancelText="取消"
        centered
      >
        <Input.TextArea
          value={mtTopicDraft}
          onChange={(e) => setMtTopicDraft(e.target.value)}
          placeholder="例如：科幻 / 法庭 / 医疗 / 游戏解说"
          autoSize={{ minRows: 3, maxRows: 6 }}
        />
        <Text type="secondary">仅影响当前批次的翻译风格。</Text>
      </Modal>

      <Modal
        title="清理中间产物"
        open={cleanupDialogOpen}
        okText="开始清理"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          disabled: !cleanupIncludeDiagnostics && !cleanupIncludeResume && !cleanupIncludeReview,
        }}
        onOk={confirmCleanupArtifacts}
        onCancel={() => setCleanupDialogOpen(false)}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">仅对当前任务生效，不会删除交付物。</Text>
          <Checkbox checked={cleanupIncludeDiagnostics} onChange={(e) => setCleanupIncludeDiagnostics(e.target.checked)}>
            清理诊断/日志文件（建议）
          </Checkbox>
          <Checkbox checked={cleanupIncludeResume} onChange={(e) => setCleanupIncludeResume(e.target.checked)}>
            清理断点续跑文件（可能无法继续从上次继续）
          </Checkbox>
          <Checkbox checked={cleanupIncludeReview} onChange={(e) => setCleanupIncludeReview(e.target.checked)}>
            清理审校文件（将丢失审校稿）
          </Checkbox>
        </Space>
      </Modal>

      <Modal
        title={
          overrideEditing?.kind === "wizard"
            ? `单个设置：${wizardTasks[overrideEditing.wizardIdx]?.inputName || ""}`
            : overrideEditing?.kind === "batch"
              ? `单个设置：${(batchesRef.current.find((x) => x.id === overrideEditing.batchId)?.tasks?.[overrideEditing.taskIndex]?.inputName) || ""}`
              : "单个设置"
        }
        open={overrideModalOpen}
        onCancel={() => {
          setOverrideModalOpen(false);
          setOverrideEditing(null);
        }}
        footer={[
          <Button
            key="clear"
            onClick={() => {
              if (!overrideEditing) return;
              if (overrideEditing.kind === "wizard") {
                applyEraseSubOverrideToWizard(overrideEditing.wizardIdx, {});
              } else {
                applyEraseSubOverrideToBatch(overrideEditing.batchId, overrideEditing.taskIndex, {});
              }
              message.success("已清除单个设置（将跟随批次设置）");
              setOverrideModalOpen(false);
              setOverrideEditing(null);
            }}
          >
            清除单个设置
          </Button>,
          <Button
            key="cancel"
            onClick={() => {
              setOverrideModalOpen(false);
              setOverrideEditing(null);
            }}
          >
            取消
          </Button>,
          <Button
            key="ok"
            type="primary"
            onClick={async () => {
              try {
                const vals = await overrideForm.validateFields();
                if (!overrideEditing) return;
                if (overrideEditing.kind === "wizard") {
                  applyEraseSubOverrideToWizard(overrideEditing.wizardIdx, vals);
                } else {
                  applyEraseSubOverrideToBatch(overrideEditing.batchId, overrideEditing.taskIndex, vals);
                }
                message.success("已保存单个设置");
                setOverrideModalOpen(false);
                setOverrideEditing(null);
              } catch {
                // ignore
              }
            }}
          >
            保存
          </Button>,
        ]}
        width={760}
      >
        <Alert
          type="info"
          showIcon
          message="这里的设置会覆盖本批次设置（仅对当前视频生效）。"
          description="如果该任务已开始，新的设置会在“从上次继续/重新生成”时生效。"
          style={{ marginBottom: 12 }}
        />
        <Form form={overrideForm} layout="vertical">
          <Card
            size="small"
            title="字幕样式（成片烧录）"
            style={{ marginBottom: 12 }}
            extra={
              <Button
                size="small"
                onClick={() =>
                  overrideForm.setFieldsValue({
                    sub_font_size: 18,
                    sub_margin_v: 24,
                    sub_outline: 1,
                    sub_alignment: 2,
                    sub_place_enable: false,
                  })
                }
              >
                恢复推荐默认
              </Button>
            }
          >
            <Row gutter={12}>
              <Col span={6}>
                <Form.Item label="字号" name="sub_font_size">
                  <InputNumber style={{ width: "100%" }} min={10} max={40} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="底部边距（px）" name="sub_margin_v">
                  <InputNumber style={{ width: "100%" }} min={0} max={120} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="描边" name="sub_outline">
                  <InputNumber style={{ width: "100%" }} min={0} max={6} />
                </Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item label="对齐" name="sub_alignment">
                  <Select
                    options={[
                      { label: "底部居中（推荐）", value: 2 },
                      { label: "底部左侧", value: 1 },
                      { label: "底部右侧", value: 3 },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={12} align="middle">
              <Col span={10}>
                <Form.Item label="字幕位置：使用矩形（优先）" name="sub_place_enable" valuePropName="checked">
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              </Col>
              <Col span={14}>
                <Button onClick={() => openRegionPickerFor("subtitle", "override", currentOverrideLocalPath())}>可视化选择字幕矩形</Button>
              </Col>
            </Row>
            </Card>

          <Row gutter={12}>
            <Col span={8}>
              <Form.Item label="硬字幕擦除" name="erase_subtitle_enable" valuePropName="checked">
                <Switch checkedChildren="开启" unCheckedChildren="关闭" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="擦除方法" name="erase_subtitle_method">
                <Select options={[{ label: "delogo（推荐）", value: "delogo" }]} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="坐标模式" name="erase_subtitle_coord_mode">
                <Select
                  options={[
                    { label: "比例（ratio）", value: "ratio" },
                    { label: "像素（px）", value: "px" },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Space wrap style={{ marginBottom: 12 }}>
            <Button
              onClick={() => openRegionPicker("override", currentOverrideLocalPath())}
            >
              可视化定位（视频+拖拽框）
            </Button>
            <Text type="secondary">提示：仅桌面版且需能获取到本地视频路径。</Text>
          </Space>
          <Row gutter={12}>
            <Col span={6}>
              <Form.Item label="区域 X（起点）" name="erase_subtitle_x">
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label="区域 Y（起点）" name="erase_subtitle_y">
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label="区域 宽度" name="erase_subtitle_w">
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item label="区域 高度" name="erase_subtitle_h">
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item label="模糊半径（px）" name="erase_subtitle_blur_radius">
                <InputNumber style={{ width: "100%" }} min={0} />
              </Form.Item>
            </Col>
            <Col span={16}>
              <Alert
                type="warning"
                showIcon
                message="高风险功能"
                description="擦除可能伤画面；建议只在确实有烧录字幕且必须去除时使用，并先用短视频试跑。"
              />
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title={regionPickerPurpose === "subtitle" ? "字幕位置矩形：可视化定位（拖动进度条定位）" : "硬字幕擦除区域：可视化定位（拖动进度条定位）"}
        open={regionPickerOpen}
        onCancel={() => setRegionPickerOpen(false)}
        onOk={() => {
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
        }}
        okText="应用到表单"
        cancelText="关闭"
        width={820}
        styles={{ body: { maxHeight: "75vh", overflowY: "auto" } }}
      >
        <Alert
          type="info"
          showIcon
          message="操作方法"
          description="拖动视频进度条找到字幕出现的位置；遮挡区域是固定高度的小矩形，不会影响你拖动进度条。你可以用下方滑条微调矩形的上下位置。输出为比例坐标（ratio），更稳定。"
          style={{ marginBottom: 12 }}
        />
        <Space direction="vertical" size="small" style={{ width: "100%", marginBottom: 12 }}>
          <Space wrap>
            <Button icon={<UploadOutlined />} onClick={() => regionPickerFileInputRef.current?.click()}>
              选择预览视频…
            </Button>
            <Button onClick={resetRegionPickerVideo} disabled={!regionPickerVideoPath}>
              清除
            </Button>
            <Text type="secondary">预览视频仅用于定位坐标，不上传后端。</Text>
          </Space>
          <Text type={regionPickerVideoError ? "danger" : regionPickerVideoReady ? "success" : "secondary"}>
            {regionPickerVideoError
              ? `加载失败：${regionPickerVideoError}`
              : regionPickerVideoReady
                ? `已加载：${regionPickerVideoInfo.name || "预览视频"}（时长 ${(regionPickerVideoInfo.duration || 0).toFixed(2)}s，${regionPickerVideoInfo.w || "?"}×${regionPickerVideoInfo.h || "?"}）`
                : regionPickerVideoPath
                  ? "加载中…（如果进度条不可拖，多半是还没加载成功）"
                  : "未选择预览视频（可手动选择，或依赖自动路径）。"}
          </Text>
        </Space>

        <Row gutter={12} style={{ marginBottom: 12 }}>
          <Col span={24}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Text>上下位置（y）</Text>
              <Slider
                min={0}
                max={Math.max(0, 1 - regionPickerRect.h)}
                step={0.001}
                value={regionPickerRect.y}
                onChange={(v) => setRegionRectSafe({ y: Number(v) })}
              />
              <Row gutter={12}>
                <Col span={12}>
                  <Text>区域宽度（w）</Text>
                  <Slider min={0.05} max={1.0} step={0.001} value={regionPickerRect.w} onChange={(v) => setRegionRectSafe({ w: Number(v) })} />
                </Col>
                <Col span={12}>
                  <Text>区域高度（h）</Text>
                  <Slider min={0.03} max={0.6} step={0.001} value={regionPickerRect.h} onChange={(v) => setRegionRectSafe({ h: Number(v) })} />
                </Col>
              </Row>
              <Space wrap>
                <Text>最终字幕字号（会影响成片）：</Text>
                <InputNumber
                  size="small"
                  min={10}
                  max={60}
                  value={regionPickerSampleFontSize}
                  onChange={(v) => setFinalSubtitleFontSize(Number(v || 18))}
                />
                <Input
                  size="small"
                  style={{ width: 280 }}
                  value={regionPickerSampleText}
                  onChange={(e) => setRegionPickerSampleText(e.target.value)}
                />
                <Text type="secondary">（示例文字会显示在矩形中）</Text>
              </Space>
              <Text type="secondary">提示：这里的矩形大小由 w/h 控制（与表单“区域宽度/高度”一致）；y 只控制上下位置。</Text>
            </Space>
          </Col>
        </Row>

        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <div
            style={{
              position: "relative",
              width: "100%",
              maxWidth: 760,
              margin: "0 auto",
              background: "#000",
              borderRadius: 8,
              overflow: "hidden",
              userSelect: "none",
            }}
          >
            {regionPickerVideoPath ? (
              <video
                ref={regionPickerVideoRef}
                src={regionPickerVideoPath}
                controls
                preload="metadata"
                style={{
                  width: "100%",
                  height: "auto",
                  maxHeight: "55vh",
                  display: "block",
                  objectFit: "contain",
                }}
                onLoadedMetadata={() => {
                  setRegionPickerVideoReady(true);
                  setRegionPickerVideoError("");
                  const v = regionPickerVideoRef.current;
                  if (v) {
                    setRegionPickerVideoInfo((prev) => ({
                      ...prev,
                      duration: Number.isFinite(v.duration) ? v.duration : undefined,
                      w: v.videoWidth || undefined,
                      h: v.videoHeight || undefined,
                    }));
                  }
                }}
                onError={() => {
                  setRegionPickerVideoReady(false);
                  const v = regionPickerVideoRef.current as any;
                  const code = v?.error?.code;
                  const msg = v?.error?.message;
                  setRegionPickerVideoError(`视频加载失败（code=${code || "?"}${msg ? `, ${msg}` : ""}）。可点上方“选择预览视频…”重试。`);
                }}
              />
            ) : (
              <div style={{ padding: 18 }}>
                <Text type="secondary">请先选择一个预览视频。</Text>
              </div>
            )}
            <div
              style={{
                position: "absolute",
                left: `${regionPickerVideoBox.x + regionPickerRect.x * regionPickerVideoBox.w}px`,
                top: `${regionPickerVideoBox.y + regionPickerRect.y * regionPickerVideoBox.h}px`,
                width: `${regionPickerRect.w * regionPickerVideoBox.w}px`,
                height: `${regionPickerRect.h * regionPickerVideoBox.h}px`,
                border: "2px solid #faad14",
                background: "rgba(250, 173, 20, 0.15)",
                pointerEvents: "none",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: 6,
                boxSizing: "border-box",
              }}
            >
              <div
                style={{
                  color: "#fff",
                  fontSize: regionPickerSampleFontSize * regionPickerVideoScale,
                  fontWeight: 400,
                  lineHeight: 1.0,
                  textAlign: "center",
                  textShadow: "0 0 0 rgba(0,0,0,0.6)",
                  whiteSpace: "pre-wrap",
                  maxWidth: "100%",
                }}
              >
                {regionPickerSampleText}
              </div>
            </div>
          </div>
        </Space>
        {!!regionPickerVideoPath && (
          <div style={{ marginTop: 10 }}>
            {!regionPickerVideoReady && !regionPickerVideoError && <Text type="secondary">视频加载中…</Text>}
            {!!regionPickerVideoError && <Text type="danger">{regionPickerVideoError}</Text>}
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <Text type="secondary">
            当前（ratio）：x={regionPickerRect.x.toFixed(3)} y={regionPickerRect.y.toFixed(3)} w={regionPickerRect.w.toFixed(3)} h=
            {regionPickerRect.h.toFixed(3)}
          </Text>
        </div>
      </Modal>
    </Layout>
  );
};

function TaskDrawerContent(props: {
  batch: BatchModel;
  taskIndex: number;
  initialTab?: string;
  onOpenOutput: (relDir?: string) => void;
  logText: string;
  logLoading: boolean;
  onResume: (resumeFrom: "asr" | "mt" | "tts" | "mux") => void;
  onRunReview: (lang: "chs" | "eng") => void;
  onApplyReview: (action: "mux" | "embed" | "mux_embed", use?: "review" | "base") => void;
  onExportDiagnostic: (opts?: { includeMedia?: boolean }) => void;
  onCleanup: (taskIndex: number) => void;
}) {
  const { batch, taskIndex, initialTab, onOpenOutput, logText, logLoading, onResume, onRunReview, onApplyReview, onExportDiagnostic, onCleanup } = props;
  const t = batch.tasks[taskIndex];
  const arts = t.artifacts || [];
  const downloadUrl = (path: string) => `${apiBase}/api/tasks/${t.taskId}/download?path=${encodeURIComponent(path)}`;

  // Review tab state (kept local to drawer)
  const [reviewLang, setReviewLang] = useState<"eng" | "chs">("chs");
  const [reviewWhich] = useState<"base" | "review">("review");
  const [reviewText, setReviewText] = useState<string>("");
  const [reviewLoading, setReviewLoading] = useState(false);

  const [termsText, setTermsText] = useState<string>("");
  const [termsLoading, setTermsLoading] = useState(false);
  const [termsError, setTermsError] = useState<string>("");
  const [activeTab, setActiveTab] = useState<string>("quality");
  const reviewEnabled = (batch.params?.review_enabled ?? true) !== false;
  const [qualityLoading, setQualityLoading] = useState(false);
  const [qualityReport, setQualityReport] = useState<{ passed?: boolean; errors?: string[]; warnings?: string[] } | null>(null);

  useEffect(() => {
    if (activeTab !== "review") return;
    if (!reviewEnabled) return;
    if (!t.taskId) return;
    if (!["completed", "failed"].includes(t.state)) return;
    (async () => {
      setReviewLoading(true);
      try {
        if (reviewLang === "eng") {
          const res = await getEngSrt(t.taskId, reviewWhich);
          setReviewText(res.content || "");
        } else {
          const res = await getChsSrt2(t.taskId, reviewWhich);
          setReviewText(res.content || "");
        }
      } catch {
        setReviewText("");
      } finally {
        setReviewLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, t.taskId, t.state, reviewLang, reviewWhich, reviewEnabled]);

  useEffect(() => {
    if (!initialTab) return;
    if (initialTab === "review" && !reviewEnabled) {
      setActiveTab("quality");
      return;
    }
    setActiveTab(initialTab);
  }, [initialTab, reviewEnabled, t.taskId]);

  useEffect(() => {
    if (activeTab !== "quality") return;
    if (!t.taskId) return;
    if (!["completed", "failed"].includes(t.state)) return;
    if (qualityReport) return;
    (async () => {
      setQualityLoading(true);
      try {
        const qr = await getQualityReport(t.taskId);
        if (qr) {
          setQualityReport({
            passed: !!qr.passed,
            errors: qr.errors || [],
            warnings: qr.warnings || [],
          });
        } else {
          setQualityReport(null);
        }
      } catch {
        setQualityReport(null);
      } finally {
        setQualityLoading(false);
      }
    })();
  }, [activeTab, t.taskId, t.state, qualityReport]);

  async function loadTerminology() {
    if (!t.taskId) return;
    setTermsLoading(true);
    setTermsError("");
    try {
      const res = await getTerminology(t.taskId);
      setTermsText(res.content || "");
    } catch (err: any) {
      // 404 is common when feature not enabled
      setTermsText("");
      setTermsError(err?.message || "暂无术语文件");
    } finally {
      setTermsLoading(false);
    }
  }

  return (
    <Tabs
      activeKey={activeTab}
      onChange={(k) => {
        setActiveTab(k);
        if (k === "terms" && !termsText && !termsLoading) loadTerminology();
      }}
      items={[
        {
          key: "quality",
          label: "质量检查",
          children: (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              {!["completed", "failed"].includes(t.state) ? (
                <Text type="secondary">任务未结束，质量报告将在完成后生成。</Text>
              ) : qualityLoading ? (
                <Text type="secondary">质量报告加载中…</Text>
              ) : (qualityReport?.passed ?? t.qualityPassed) == null ? (
                <Text type="secondary">暂无质量报告（任务结束后会自动生成）。</Text>
              ) : (
                <>
                  <Alert
                    type={(qualityReport?.passed ?? t.qualityPassed) ? "success" : "warning"}
                    showIcon
                    message={(qualityReport?.passed ?? t.qualityPassed) ? "通过：可交付" : "未通过：建议先处理问题再交付"}
                  />

                  <Card size="small" title="结论与建议">
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Text>
                        {(qualityReport?.passed ?? t.qualityPassed)
                          ? "结论：质量检查已通过，建议交付。"
                          : "结论：存在影响交付的问题，建议先处理后再交付。"}
                      </Text>
                      <Text type="secondary">建议：优先处理「主要问题」，再查看「风险提示」。</Text>
                    </Space>
                  </Card>

                  {(qualityReport?.errors || t.qualityErrors) && (qualityReport?.errors || t.qualityErrors).length > 0 ? (
                    <Card size="small" title="主要问题（需要处理）">
                      <List
                        size="small"
                        dataSource={(qualityReport?.errors || t.qualityErrors).slice(0, 10)}
                        renderItem={(x) => {
                          const tag = issueTag(x);
                          return (
                            <List.Item>
                              <Space direction="vertical" size={2}>
                                <Space wrap>
                                  <Tag color={tag.color}>{tag.label}</Tag>
                                  <Text>{x}</Text>
                                </Space>
                                <Text type="secondary">{suggestForIssue(x)}</Text>
                              </Space>
                            </List.Item>
                          );
                        }}
                      />
                    </Card>
                  ) : (
                    <Text type="secondary">主要问题：无</Text>
                  )}

                  {(qualityReport?.warnings || t.qualityWarnings) && (qualityReport?.warnings || t.qualityWarnings).length > 0 ? (
                    <Card size="small" title="风险提示（可交付但建议关注）">
                      <List
                        size="small"
                        dataSource={(qualityReport?.warnings || t.qualityWarnings).slice(0, 10)}
                        renderItem={(x) => {
                          const tag = issueTag(x);
                          return (
                            <List.Item>
                              <Space direction="vertical" size={2}>
                                <Space wrap>
                                  <Tag color={tag.color}>{tag.label}</Tag>
                                  <Text>{x}</Text>
                                </Space>
                                <Text type="secondary">{suggestForIssue(x)}</Text>
                              </Space>
                            </List.Item>
                          );
                        }}
                      />
                    </Card>
                  ) : (
                    <Text type="secondary">风险提示：无</Text>
                  )}
                </>
              )}
            </Space>
          ),
        },
        ...(reviewEnabled
          ? [
              {
                key: "review",
                label: "审校",
                children: (
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert type="info" showIcon message="校审：在这里直接修改字幕内容，点击“更新成片”即可生效。" />
                    {!["completed", "failed"].includes(t.state) && (
                      <Alert type="warning" showIcon message="任务未完成，完成后可进行审校。" />
                    )}

                    <Space wrap>
                      <Radio.Group value={reviewLang} onChange={(e) => setReviewLang(e.target.value)} optionType="button" buttonStyle="solid">
                        <Radio.Button value="chs">中文字幕</Radio.Button>
                        <Radio.Button value="eng">英文字幕</Radio.Button>
                      </Radio.Group>
                    </Space>

                    <Input.TextArea
                      value={reviewText}
                      onChange={(e) => setReviewText(e.target.value)}
                      autoSize={{ minRows: 12, maxRows: 20 }}
                      placeholder={reviewLoading ? "加载中…" : "在这里直接修改字幕内容（SRT 格式）"}
                      disabled={!["completed", "failed"].includes(t.state)}
                    />

                    <Space wrap>
                      <Button
                        type="primary"
                        disabled={!t.taskId || !["completed", "failed"].includes(t.state) || !reviewText.trim()}
                        onClick={async () => {
                          if (!t.taskId) return;
                          try {
                            setReviewLoading(true);
                            if (reviewLang === "eng") {
                              await putEngReviewSrt(t.taskId, reviewText);
                              await onApplyReview("mux_embed", "review");
                            } else {
                              await putChsReviewSrt(t.taskId, reviewText);
                              await onRunReview("chs");
                            }
                          } catch (err: any) {
                            message.error(err?.message || "更新失败");
                          } finally {
                            setReviewLoading(false);
                          }
                        }}
                      >
                        更新成片
                      </Button>
                    </Space>

                  </Space>
                ),
              },
            ]
          : []),
        {
          key: "terms",
          label: "术语",
          children: (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Alert
                type="info"
                showIcon
                message="这里仅查看任务内术语（只读）。编辑术语请在“新建批次 → 术语”里完成。"
              />
              <Text type="secondary">若未开启“译前暂停”，此处可能为空。</Text>
              {termsError && <Alert type="warning" showIcon message={termsError} />}
              <Input.TextArea
                value={termsText}
                readOnly
                autoSize={{ minRows: 10, maxRows: 18 }}
                placeholder={termsLoading ? "加载中…" : "这里是 terminology.json 原文（只读）。"}
              />
              <Space wrap>
                <Button onClick={loadTerminology} disabled={!t.taskId}>
                  重新加载
                </Button>
              </Space>
            </Space>
          ),
        },
        {
          key: "log",
          label: "日志",
          children: (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Space wrap>
                <Button onClick={() => onExportDiagnostic({ includeMedia: false })}>导出诊断包（zip）</Button>
                <Button danger onClick={() => onExportDiagnostic({ includeMedia: true })}>
                  导出诊断包（含成片/音频）
                </Button>
                <Button danger icon={<DeleteOutlined />} disabled={!t.taskId} onClick={() => onCleanup(taskIndex)}>
                  清理中间产物
                </Button>
              </Space>
              <div style={{ border: "1px solid #f0f0f0", background: "#0f172a", color: "#e5e7eb", borderRadius: 6, minHeight: 240, padding: 12, overflow: "auto", fontFamily: "ui-monospace" }}>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {logLoading ? "加载中…" : (logText || "暂无日志")}
                </pre>
              </div>
            </Space>
          ),
        },
      ]}
    />
  );
}

export default App;

