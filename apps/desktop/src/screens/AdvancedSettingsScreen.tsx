import React from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Layout,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import type { FormInstance } from "antd";
import type { AppConfig } from "../types";
import type { UiPrefs } from "../batchStorage";
import { useAdvancedCtx } from "../app/contexts/AdvancedContext";

const { Content } = Layout;
const { Text } = Typography;

type RiskLevel = "低" | "中" | "高" | "实验" | "未知";

export function AdvancedScreen() {
  const {
    config,
    mode,
    advancedShowAll,
    setAdvancedShowAll,
    form,
    toggleMeta,
    paramMeta,
    textMeta,
    stageOfToggle,
    uiPrefs,
    setUiPrefs,
    saveUiPrefs,
    devToolsEnabled,
  } = useAdvancedCtx();

  const isDockerDev =
    String(config?.runtime?.repo_root || "") === "/app" ||
    String(config?.runtime?.cwd || "") === "/app" ||
    String(config?.runtime?.YGF_APP_ROOT || "") === "/app";
  const developerModeActive = !!devToolsEnabled || !!isDockerDev;
  const includeOptionals = Boolean((uiPrefs as UiPrefs)?.deliveryIncludeOptionals);
  const showTaskLogs = Boolean((uiPrefs as UiPrefs)?.showTaskLogs);

  function renderUtilityCards() {
    return (
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Card
          size="small"
          title={
            <Space size="small">
              <Text strong>交付附带中间产物</Text>
              <Tooltip title="默认关闭。开启后，保存到输出目录时会额外带上无字幕成片、抽取音频、中文/双语字幕等复查与复用文件。">
                <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
              </Tooltip>
            </Space>
          }
          extra={
            <Switch
              checked={includeOptionals}
              onChange={(v) => {
                const next = { ...(uiPrefs || {}), deliveryIncludeOptionals: !!v };
                setUiPrefs(next);
                saveUiPrefs(next);
                message.success(v ? "已开启：交付附带中间产物" : "已关闭：仅保存最小交付");
              }}
            />
          }
        >
          <Text type="secondary">默认关闭。建议只在复查、回归测试或需要保留更多复用文件时开启。</Text>
        </Card>
        <Card
          size="small"
          title={
            <Space size="small">
              <Text strong>任务详情显示日志</Text>
              <Tooltip title="默认关闭。开启后，任务中心的详情抽屉会出现“日志”页签，方便排障。">
                <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
              </Tooltip>
            </Space>
          }
          extra={
            <Switch
              checked={showTaskLogs}
              onChange={(v) => {
                const next = { ...(uiPrefs || {}), showTaskLogs: !!v };
                setUiPrefs(next);
                saveUiPrefs(next);
                message.success(v ? "已开启：显示日志" : "已关闭：隐藏日志");
              }}
            />
          }
        >
          <Text type="secondary">默认关闭。出现报错、卡住或结果异常时再打开，平时保持简洁即可。</Text>
        </Card>
      </Space>
    );
  }

  // Product: quality mode configuration is intentionally collapsed to two toggles in the wizard:
  // - 字幕更易读（推荐）
  // - 配音更自然（推荐）
  // Advanced settings are for developers only.
  if (mode === "quality" && !developerModeActive) {
    return (
      <Content style={{ padding: 16 }}>
        <Card title="高级设置" extra={<Text type="secondary">已收敛</Text>}>
          <Alert
            type="info"
            showIcon
            message="质量模式已默认启用推荐策略"
            description={
              <Space direction="vertical" size={4}>
                <Text>字幕更易读 / 配音更自然：已固定开启（不再提供开关）。</Text>
                <Text type="secondary">其余细项为了稳定与易用性，已不对普通用户开放。</Text>
              </Space>
            }
          />
          <div style={{ marginTop: 12 }}>
            {renderUtilityCards()}
          </div>
        </Card>
      </Content>
    );
  }

  return (
    <Content style={{ padding: 16 }}>
      <Card
        title="高级设置"
        extra={
          <Text type="secondary">
            {mode === "quality" ? "按类别整理（字幕 / 配音）" : '按阶段整理配置（会随"当前模式"切换）'}
          </Text>
        }
      >
        {!config?.defaults ? (
          <Alert type="warning" showIcon message="配置尚未加载" description='请稍等，或点击右上角"重新检测"。' />
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message={`当前模式：${mode === "lite" ? "轻量" : mode === "quality" ? "质量" : "在线"}`}
              description={
                mode === "lite"
                  ? "轻量模式只开放 5 个用户配置项，其余策略已固定收敛。日志与中间产物开关放在“其他”。"
                  : "只展示当前模式可生效的配置。建议先保持默认，用到再改。"
              }
              style={{ marginBottom: 12 }}
            />

            <Form form={form} layout="vertical">
              {(() => {
                const defaults = config!.defaults || {};
                const SUPPORT = {
                  lite: {
                    bool: new Set<string>(["skip_tts"]),
                    num: new Set<string>(["whispercpp_threads", "min_sub_duration", "tts_split_len", "tts_speed_max"]),
                    str: new Set<string>([]),
                  },
                  quality: {
                    // 质量模式：已收敛为「字幕更易读 / 配音更自然」两项主开关；
                    // 高级设置仅保留其“细分数值项”（用于小范围微调），不再暴露高消耗/不稳定/低收益开关。
                    bool: new Set<string>([]),
                    num: new Set<string>([
                      // 字幕更易读（细分项）
                      "subtitle_wrap_max_lines",
                      "display_max_chars_per_line",
                      "display_max_lines",
                      "display_merge_max_gap_s",
                      "display_merge_max_chars",
                      "display_split_max_chars",
                      // 配音更自然（细分项）
                      "tts_fit_wps",
                      "tts_fit_min_words",
                      "tts_plan_safety_margin",
                      "tts_plan_min_cap",
                      // 安全阈值（允许微调）
                      "min_sub_duration",
                      "tts_speed_max",
                    ]),
                    str: new Set<string>([]),
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

                // 质量模式 Docker 开发者环境：开发者页只展示“后端实际会透传到 quality pipeline 的键”。
                // 这样可以避免把 lite/历史/已下线的键暴露出来，造成“改了也不生效 / 引入回归”的困惑。
                const QUALITY_DEV_ALLOW = {
                  bool: new Set<string>([
                    "asr_normalize_enable",
                    "denoise",
                    // zh_polish / review gate
                    "zh_phrase_force_one_per_line",
                    "zh_phrase_idiom_enable",
                    // P0/P1/P2
                    "subtitle_postprocess_enable",
                    "subtitle_wrap_enable",
                    "display_srt_enable",
                    "display_use_for_embed",
                    "display_merge_enable",
                    "display_split_enable",
                    "tts_fit_enable",
                    "tts_fit_save_raw",
                    "tts_plan_enable",
                  ]),
                  num: new Set<string>([
                    // ASR
                    "max_sentence_len",
                    "min_sub_duration",
                    "llm_chunk_size",
                    // MT/LLM
                    "mt_context_window",
                    "mt_max_words_per_line",
                    // zh_polish / review gate
                    "zh_phrase_max_spans",
                    "zh_phrase_max_total",
                    "zh_phrase_chunk_lines",
                    "zh_phrase_candidate_max_lines",
                    // TTS
                    "tts_speed_max",
                    "tts_fit_wps",
                    "tts_fit_min_words",
                    "tts_plan_safety_margin",
                    "tts_plan_min_cap",
                    // Subtitle post-process / display
                    "subtitle_wrap_max_lines",
                    "display_max_chars_per_line",
                    "display_max_lines",
                    "display_merge_max_gap_s",
                    "display_merge_max_chars",
                    "display_split_max_chars",
                    // Mux sync
                    "mux_slow_max_ratio",
                    "mux_slow_threshold_s",
                  ]),
                  str: new Set<string>([
                    // Models
                    "whisperx_model",
                    // LLM/MT
                    "llm_endpoint",
                    "llm_model",
                    "zh_phrase_llm_model",
                    "llm_api_key",
                    "mt_style",
                    "zh_phrase_idiom_path",
                    "zh_phrase_same_pinyin_path",
                  ]),
                } as const;

                const support = mode === "quality" ? SUPPORT.quality : mode === "online" ? SUPPORT.online : SUPPORT.lite;

                const DEV_ONLY_KEYS = new Set<string>([
                  "asr_merge_save_debug",
                  "asr_llm_fix_save_debug",
                  "meaning_split_save_debug",
                  "tts_fit_save_raw",
                  "tra_save_debug",
                  "qe_save_report",
                  "mt_skip_if_present",
                  "mt_pause_before_translate",
                  "diarization",
                  "qe_backtranslate_enable",
                  "dedupe",
                  "display_merge_enable",
                  "bgm_mix_enable",
                  "bgm_duck_enable",
                  "bgm_loudnorm_enable",
                  "qe_backtranslate_enable",
                  "display_srt_enable",
                  "subtitle_wrap_enable",
                  "subtitle_cps_fix_enable",
                  "display_max_chars_per_line",
                  "display_max_lines",
                  "display_merge_max_gap_s",
                  "display_merge_max_chars",
                  "display_split_max_chars",
                  "tts_gain_db",
                  "bgm_gain_db",
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
                  "erase_subtitle_enable",
                  "erase_subtitle_method",
                  "erase_subtitle_coord_mode",
                  "erase_subtitle_x",
                  "erase_subtitle_y",
                  "erase_subtitle_w",
                  "erase_subtitle_h",
                  "erase_subtitle_blur_radius",
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

                const COMMON_QUALITY_BOOL = new Set<string>([]);
                const COMMON_QUALITY_NUM = new Set<string>([
                  "min_sub_duration",
                  "tts_speed_max",
                  "tts_fit_wps",
                  "tts_fit_min_words",
                  "tts_plan_safety_margin",
                  "tts_plan_min_cap",
                  "subtitle_wrap_max_lines",
                  "display_max_chars_per_line",
                  "display_max_lines",
                  "display_merge_max_gap_s",
                  "display_merge_max_chars",
                  "display_split_max_chars",
                ]);
                const COMMON_QUALITY_STR = new Set<string>([]);
                const showAll = mode === "quality" ? advancedShowAll : true;
                const inCommon = (k: string) =>
                  mode === "quality" ? COMMON_QUALITY_BOOL.has(k) || COMMON_QUALITY_NUM.has(k) || COMMON_QUALITY_STR.has(k) : true;
                const filterCommon = (list: string[]) => (showAll ? list : list.filter(inCommon));

                const isQuality = mode === "quality";
                const stageOrder: string[] = isQuality ? ["字幕", "配音", "其他"] : ["语音识别", "翻译", "配音", "合成", "其他"];
                const stageLabel: Record<string, string> = isQuality
                  ? { 字幕: "字幕", 配音: "配音", 其他: "其他", 开发者: "开发者", 常用: "常用", "开发者（仅用于排查）": "开发者" }
                  : {
                      语音识别: "语音识别",
                      翻译: "翻译",
                      配音: "配音",
                      合成: "合成",
                      其他: "其他",
                      常用: "常用",
                      "开发者（仅用于排查）": "开发者",
                    };
                const tabs: string[] = isQuality
                  ? [...stageOrder, ...(developerModeActive ? ["开发者"] : [])]
                  : stageOrder.filter(
                      (s) =>
                        s === "其他" ||
                        filterCommon(byStageBool[s] || []).length > 0 ||
                        filterCommon(byStageNum[s] || []).length > 0 ||
                        filterCommon(byStageStr[s] || []).length > 0,
                    );
                if (!isQuality && devToolsEnabled && showAll) tabs.push("开发者（仅用于排查）");

                const DEPENDS_ON: Record<string, string> = {};
                const CHILDREN: Record<string, string[]> = {};

                function dependencyHint(k: string, getFieldValue: any): string {
                  const parent = DEPENDS_ON[k];
                  if (!parent) return "";
                  if (!!getFieldValue(parent)) return "";
                  return `需要先开启「${toggleMeta[parent]?.label || "前置开关"}」`;
                }

                function renderBoolCard(k: string) {
                  const meta =
                    toggleMeta[k] || ({ label: "未命名配置项", desc: "该项尚未补齐中文说明。建议保持默认。", risk: "未知" } as const);
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
                                        <div>
                                          <b>提示：</b>
                                          {meta.desc || "按需调整"}
                                        </div>
                                        {recommend && (
                                          <div>
                                            <b>建议：</b>
                                            {recommend}
                                          </div>
                                        )}
                                        {riskHint && <div style={{ marginTop: 6 }}>{riskHint}</div>}
                                        {hint && (
                                          <div style={{ marginTop: 6 }}>
                                            <b>联动：</b>
                                            {hint}
                                          </div>
                                        )}
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

                const DEPENDS_ON_PARAM: Record<string, string> = {};

                function renderTextCard(k: string) {
                  const meta = textMeta[k] || { label: k, kind: "text" as const };
                  const input =
                    meta.kind === "password" ? (
                      <Input.Password style={{ width: 260 }} placeholder={meta.placeholder || "保持默认"} />
                    ) : meta.kind === "select" ? (
                      <Select style={{ width: 260 }} options={meta.options || []} placeholder={meta.placeholder || "保持默认"} />
                    ) : (
                      <Input style={{ width: 260 }} placeholder={meta.placeholder || "保持默认"} />
                    );
                  return (
                    <Form.Item key={k} noStyle shouldUpdate={(prev, cur) => prev?.[k] !== cur?.[k]}>
                      {() => (
                        <Card size="small">
                          <Space style={{ width: "100%", justifyContent: "space-between" }} align="start">
                            <Space direction="vertical" size={6} style={{ maxWidth: 620 }}>
                              <Space align="center" wrap>
                                <Text strong>{meta.label}</Text>
                                <Tooltip
                                  title={
                                    <div style={{ maxWidth: 360 }}>
                                      <div>
                                        <b>提示：</b>
                                        {meta.desc || "按需配置"}
                                      </div>
                                      {meta.recommend && (
                                        <div>
                                          <b>建议：</b>
                                          {meta.recommend}
                                        </div>
                                      )}
                                      {meta.riskHint && <div style={{ marginTop: 6 }}>{meta.riskHint}</div>}
                                      {!textMeta[k] && (
                                        <div style={{ marginTop: 6 }}>
                                          <b>内部键：</b>
                                          {k}
                                        </div>
                                      )}
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
                      )}
                    </Form.Item>
                  );
                }

                function renderNumberCard(k: string) {
                  const meta =
                    paramMeta[k] ||
                    ({
                      label: k,
                      desc: "开发者项：该配置项尚未补齐中文说明。",
                      risk: "未知" as const,
                    } as any);
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
                                        <div>
                                          <b>提示：</b>
                                          {meta.desc || "按需调整"}
                                        </div>
                                        {meta.recommend && (
                                          <div>
                                            <b>建议：</b>
                                            {meta.recommend}
                                          </div>
                                        )}
                                        {meta.riskHint && <div style={{ marginTop: 6 }}>{meta.riskHint}</div>}
                                        {hint && (
                                          <div style={{ marginTop: 6 }}>
                                            <b>联动：</b>
                                            {hint}
                                          </div>
                                        )}
                                      </div>
                                    }
                                  >
                                    <Button size="small" type="text" icon={<QuestionCircleOutlined />} aria-label="查看说明" />
                                  </Tooltip>
                                </Space>
                              </Space>
                              <Form.Item name={k} style={{ margin: 0 }}>
                                <InputNumber
                                  style={{ width: 160 }}
                                  step={step}
                                  min={min}
                                  max={max}
                                  placeholder="保持默认"
                                  disabled={disabled}
                                />
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
                const subtitleNumKeys = numUserKeys.filter((k) => k === "min_sub_duration" || k.startsWith("subtitle_") || k.startsWith("display_"));
                const dubbingNumKeys = numUserKeys.filter((k) => k.startsWith("tts_") && k !== "tts_split_len");
                const devBoolAll = (isQuality ? [...QUALITY_DEV_ALLOW.bool] : Object.keys(defaults))
                  .filter((k) => typeof (defaults as any)[k] === "boolean")
                  .filter((k) => !HIDDEN_KEYS.has(k));
                const devNumAll = (isQuality ? [...QUALITY_DEV_ALLOW.num] : Object.keys(defaults))
                  .filter((k) => typeof (defaults as any)[k] === "number")
                  .filter((k) => !HIDDEN_KEYS.has(k));
                const devStrAll = (isQuality ? [...QUALITY_DEV_ALLOW.str] : Object.keys(defaults))
                  .filter((k) => typeof (defaults as any)[k] === "string")
                  .filter((k) => !HIDDEN_KEYS.has(k));

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
                          {isQuality && stage === "字幕" && (
                            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                              {subtitleNumKeys.map((k) => renderNumberCard(k))}
                            </Space>
                          )}
                          {isQuality && stage === "配音" && (
                            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                              {dubbingNumKeys.map((k) => renderNumberCard(k))}
                            </Space>
                          )}
                          {stage === "其他" && renderUtilityCards()}
                          {isQuality && stage === "开发者" && developerModeActive && (
                            <>
                              <Alert
                                type="warning"
                                showIcon
                                message={isDockerDev ? "Docker 开发者环境：已自动展示开发者配置" : "开发者配置：仅用于排查与评测"}
                                description="这些项可能影响稳定性或引入回归，不建议在普通交付中随意更改。"
                              />
                              {(() => {
                                const devStageOrder = ["语音识别", "翻译", "配音", "合成", "开发者"];
                                const byStageDevBool: Record<string, string[]> = {};
                                const byStageDevNum: Record<string, string[]> = {};
                                const byStageDevStr: Record<string, string[]> = {};
                                for (const k of devBoolAll) { const s = stageOfToggle(k); (byStageDevBool[s] ||= []).push(k); }
                                for (const k of devNumAll) { const s = stageOfToggle(k); (byStageDevNum[s] ||= []).push(k); }
                                for (const k of devStrAll) { const s = stageOfToggle(k); (byStageDevStr[s] ||= []).push(k); }
                                return devStageOrder.map((s) => {
                                  const b = byStageDevBool[s] || [];
                                  const n = byStageDevNum[s] || [];
                                  const st = byStageDevStr[s] || [];
                                  if (b.length + n.length + st.length === 0) return null;
                                  return (
                                    <Card key={s} size="small" title={s}>
                                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                        {st.map((k) => renderTextCard(k))}
                                        {n.map((k) => renderNumberCard(k))}
                                        {orderByDependency(b).map((k) => renderBoolCard(k))}
                                      </Space>
                                    </Card>
                                  );
                                });
                              })()}
                            </>
                          )}
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
                          {!isQuality && stage === "字幕与成片" && (
                            <Alert
                              type="info"
                              showIcon
                              message="音画对齐策略已固定为整体慢放"
                              description="末尾定格已下线；这里只保留慢放比例与触发阈值可调。"
                            />
                          )}
                          {!isQuality &&
                            stage !== "开发者（仅用于排查）" &&
                            stage !== "常用" &&
                            filterCommon(byStageStr[stage] || []).length > 0 && (
                              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                {filterCommon(byStageStr[stage] || []).map((k) => renderTextCard(k))}
                              </Space>
                            )}
                          {!isQuality &&
                            stage !== "开发者（仅用于排查）" &&
                            stage !== "常用" &&
                            filterCommon(byStageNum[stage] || []).length > 0 && (
                              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                {filterCommon(byStageNum[stage] || []).map((k) => renderNumberCard(k))}
                              </Space>
                            )}
                          {!isQuality &&
                            stage !== "开发者（仅用于排查）" &&
                            stage !== "常用" &&
                            filterCommon(byStageBool[stage] || []).length > 0 && (
                              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                {orderByDependency(filterCommon(byStageBool[stage] || [])).map((k) => renderBoolCard(k))}
                              </Space>
                            )}
                          {!isQuality && stage === "开发者（仅用于排查）" && devToolsEnabled && (
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
                                const byStageDevNum: Record<string, string[]> = {};
                                const byStageDevStr: Record<string, string[]> = {};
                                for (const k of boolDevKeys) {
                                  const stage = stageOfToggle(k);
                                  (byStageDevBool[stage] ||= []).push(k);
                                }
                                for (const k of numDevKeys) {
                                  const stage = stageOfToggle(k);
                                  (byStageDevNum[stage] ||= []).push(k);
                                }
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
                                                        <Tag color={currentVal !== undefined && currentVal !== null ? "blue" : "default"}>
                                                          当前：{String(currentVal ?? "未设置")}
                                                        </Tag>
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
                                  if (typeof (defaults as any)[k] === "number" && typeof values[k] === "number" && paramMeta[k])
                                    params[k] = values[k];
                                  if (typeof (defaults as any)[k] === "string" && typeof values[k] === "string" && textMeta[k]) params[k] = values[k];
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
}

