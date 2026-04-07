import React from "react";
import { Alert, Button, Card, Col, Divider, Layout, Popconfirm, Row, Space, Tag, Typography } from "antd";
import { LogoutOutlined, ReloadOutlined } from "@ant-design/icons";
import { useSystemCtx } from "../app/contexts/SystemContext";

const { Content } = Layout;
const { Text } = Typography;

function tierLabel(t: string | undefined | null): string {
  if (!t) return "-";
  if (t === "normal") return "普通";
  if (t === "mid") return "中端";
  if (t === "high") return "高端";
  return String(t);
}

function accelLabel(v: string | undefined | null): string {
  if (v === "gpu") return "GPU";
  if (v === "cpu") return "CPU";
  if (v === "mixed") return "CPU/GPU 混合";
  if (v === "idle") return "空闲";
  return "未知";
}

function accelColor(v: string | undefined | null): string {
  if (v === "gpu") return "green";
  if (v === "cpu") return "orange";
  if (v === "mixed") return "blue";
  if (v === "idle") return "default";
  return "default";
}

function formatExpireDate(value: string | undefined | null): string {
  const raw = String(value || "").trim();
  if (!raw) return "未获取到";
  const m = raw.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : raw;
}

export function SystemScreen() {
  const {
    health,
    hardware,
    loadingBoot,
    onBootstrap,
    mode,
    requireLocalPacks,
    modelsReady,
    modelsRoot,
    modelsZipHint,
    missingLabels,
    modelsError,
    modelsImporting,
    onPickModels,
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
    ollamaStarting,
    ollamaLoading,
    onPickOllama,
    onEnsureOllama,
    onRefreshOllamaStatus,
    runtimeStatus,
    runtimeLoading,
    runtimeError,
    backendRestarting,
    onRefreshRuntimeStatus,
    onRestartBackend,
    devToolsEnabled,
    authUserEmail,
    authStatusText,
    authLicenseExpireAt,
    authLicenseLoading,
    onAuthLogout,
    canRenewInApp,
    openRenewalModal,
  } = useSystemCtx();

  const gpuDetected = !!hardware?.gpu_name;
  const devicePolicy = hardware?.device_policy || {};
  const cardStyle: React.CSSProperties = { borderRadius: 14, height: "100%" };
  const accelTitle = devicePolicy.gpu_effective ? "可用 GPU 加速" : "当前使用 CPU";
  const ollamaTitle = !ollamaPortOpen ? "本地大模型未启动" : ollamaActiveModels.length > 0 ? "本地大模型运行中" : "本地大模型已就绪";
  const gpuTagLabel = gpuDetected ? "显卡" : "无显卡";
  const isQualityMode = mode === "quality";

  return (
    <Content style={{ padding: 16 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        {!!authUserEmail ? (
          <Card
            title="账号与授权"
            extra={
              <Popconfirm
                title="退出当前账号？"
                description="退出后会回到登录页，如需继续使用可重新输入邮箱验证码登录。"
                okText="确认退出"
                cancelText="取消"
                onConfirm={async () => {
                  await onAuthLogout?.();
                }}
              >
                <Button icon={<LogoutOutlined />} loading={authLicenseLoading}>
                  退出登录
                </Button>
              </Popconfirm>
            }
          >
            <Space direction="vertical" size={6}>
              <Text>当前登录：{authUserEmail}</Text>
              <Text type="secondary">{authStatusText || "正在同步授权状态"}</Text>
              <Text type="secondary">到期时间：{formatExpireDate(authLicenseExpireAt)}</Text>
              <Text type="secondary">退出登录后不会删除本机任务数据，只会结束当前云端登录状态。</Text>
            </Space>
          </Card>
        ) : null}
        {canRenewInApp ? (
          <Alert
            type="warning"
            showIcon
            message="当前授权已到期"
            description="你现在仍可查看系统、历史和账号信息，但不能新建或继续处理任务。输入适用的激活码后会立即恢复。"
            action={
              <Button size="small" type="primary" onClick={openRenewalModal}>
                输入激活码
              </Button>
            }
          />
        ) : null}
        <Card
          title="运行状态"
          extra={
            <Button icon={<ReloadOutlined />} loading={loadingBoot} onClick={onBootstrap}>
              重新检测
            </Button>
          }
        >
          <Space align="center" wrap style={{ width: "100%", justifyContent: "space-between" }}>
            <Space align="center" wrap>
              <Tag color={health === "ok" ? "green" : "red"}>{health === "ok" ? "后端可用" : "后端不可用"}</Tag>
              <Tag color="blue">当前模式：{mode === "lite" ? "轻量" : mode === "quality" ? "质量" : "在线"}</Tag>
            </Space>
          </Space>
          <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
            <Col span={8}>
              <Card style={cardStyle} bodyStyle={{ padding: 16 }}>
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  <Text strong>硬件</Text>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flexWrap: "wrap" }}>
                    <Tag color={gpuDetected ? "green" : "default"}>{gpuTagLabel}</Tag>
                    {hardware?.gpu_vram_gb ? <Tag color="default">{hardware.gpu_vram_gb} GB</Tag> : null}
                    {gpuDetected && hardware?.gpu_name ? (
                      <Text
                        type="secondary"
                        style={{
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          flex: "1 1 auto",
                        }}
                      >
                        {hardware.gpu_name}
                      </Text>
                    ) : null}
                  </div>
                </Space>
              </Card>
            </Col>
            <Col span={8}>
              <Card style={cardStyle} bodyStyle={{ padding: 16 }}>
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  <Text strong>加速</Text>
                  <Space wrap>
                    <Tag color={devicePolicy.gpu_effective ? "green" : "default"}>{accelTitle}</Tag>
                    <Tag color="blue">LLM 自动选择</Tag>
                    {devToolsEnabled ? <Tag color="default">{tierLabel(hardware?.tier)}</Tag> : null}
                  </Space>
                </Space>
              </Card>
            </Col>
            {isQualityMode ? (
              <Col span={8}>
                <Card style={cardStyle} bodyStyle={{ padding: 16 }}>
                  <Space direction="vertical" size="small" style={{ width: "100%" }}>
                    <Text strong>本地大模型</Text>
                    <Space wrap>
                      <Tag color={ollamaReady ? "green" : "red"}>{ollamaTitle}</Tag>
                      <Tag color={accelColor(ollamaAcceleration)}>{accelLabel(ollamaAcceleration)}</Tag>
                    </Space>
                    {devToolsEnabled && !!ollamaProcessorSummary && ollamaActiveModels.length > 0 ? (
                      <Text type="secondary">{ollamaProcessorSummary}</Text>
                    ) : null}
                  </Space>
                </Card>
              </Col>
            ) : null}
          </Row>
        </Card>

        <Card title="资源与能力" extra={<Text type="secondary">决定你能用到哪些能力</Text>}>
          {!requireLocalPacks ? (
            <Text type="secondary">当前为开发环境：模型与服务由后端提供，无需手动导入。</Text>
          ) : mode !== "quality" ? (
            <Text type="secondary">轻量模式无需额外资源。切换到质量模式后，这里会提示需要的资源。</Text>
          ) : (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Space align="center" wrap>
                <Text strong>质量模型</Text>
                <Tag color={modelsReady ? "green" : "red"}>{modelsReady ? "就绪" : "未就绪"}</Tag>
                {devToolsEnabled && !!modelsRoot && <Text type="secondary">目录：{modelsRoot}</Text>}
              </Space>
              {!!modelsZipHint && <Text type="secondary">已发现资源包：{modelsZipHint}</Text>}
              {!modelsReady && missingLabels.length > 0 && <Text type="danger">缺失：{missingLabels.join("、")}</Text>}
              {!!modelsError && <Text type="danger">{modelsError}</Text>}
              <Space wrap>
                <Button type="primary" loading={modelsImporting} onClick={onPickModels}>
                  导入质量模型…
                </Button>
              </Space>

              <Divider style={{ margin: "8px 0" }} />

              <Space align="center" wrap>
                <Text strong>本地 LLM（Ollama）</Text>
                <Tag color={ollamaReady ? "green" : "red"}>{ollamaReady ? "已安装" : "未安装"}</Tag>
                <Tag color={ollamaPortOpen ? "green" : "default"}>{ollamaPortOpen ? "已启动" : "未启动"}</Tag>
                <Tag color={accelColor(ollamaAcceleration)}>运行态：{accelLabel(ollamaAcceleration)}</Tag>
              </Space>
              {!!ollamaZipHint && <Text type="secondary">已发现 Ollama 包：{ollamaZipHint}</Text>}
              {!!ollamaProcessorSummary && <Text type="secondary">处理器：{ollamaProcessorSummary}</Text>}
              {ollamaActiveModels.length > 0 && <Text type="secondary">活跃模型：{ollamaActiveModels.join("、")}</Text>}
              {!!ollamaError && <Text type="danger">{ollamaError}</Text>}
              <Space wrap>
                <Button type="primary" loading={ollamaImporting} onClick={onPickOllama}>
                  导入 Ollama…
                </Button>
                <Button loading={ollamaStarting} onClick={onEnsureOllama}>
                  启动 Ollama
                </Button>
                <Button icon={<ReloadOutlined />} loading={ollamaLoading} onClick={onRefreshOllamaStatus}>
                  刷新状态
                </Button>
                <Button
                  disabled={!ollamaRoot || !(window as any)?.bridge?.openPath || !devToolsEnabled}
                  onClick={async () => {
                    try {
                      await (window as any)?.bridge?.openPath?.(ollamaRoot);
                    } catch {}
                  }}
                >
                  打开目录（开发者）
                </Button>
              </Space>
              <Text type="secondary">提示：质量模式会用到本地 LLM。未就绪时可先用轻量模式处理。</Text>

              <Divider style={{ margin: "8px 0" }} />

              <Space align="center" wrap>
                <Text strong>Windows 打包链路</Text>
                <Button icon={<ReloadOutlined />} loading={runtimeLoading} onClick={onRefreshRuntimeStatus}>
                  刷新链路
                </Button>
                <Button loading={backendRestarting} onClick={onRestartBackend}>
                  重启 backend
                </Button>
              </Space>
              {!!runtimeError && <Text type="danger">{runtimeError}</Text>}
              {!!runtimeStatus && (
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  <Text type="secondary">
                    当前口径：{runtimeStatus.packagedWindowsProduct ? "Windows 打包产品态" : "非 Windows 打包产品态"}
                  </Text>
                  <Text type="secondary">
                    运行时清单：{runtimeStatus.manifestExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.manifestPath ? ` · ${runtimeStatus.manifestPath}` : ""}
                  </Text>
                  <Text type="secondary">
                    后端 EXE：{runtimeStatus.backendExeExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.backendExe ? ` · ${runtimeStatus.backendExe}` : ""}
                  </Text>
                  <Text type="secondary">
                    质量 Worker：{runtimeStatus.qualityWorkerExeExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.qualityWorkerExe ? ` · ${runtimeStatus.qualityWorkerExe}` : ""}
                  </Text>
                  <Text type="secondary">
                    打包配置：{runtimeStatus.packagedConfigExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.packagedConfigPath ? ` · ${runtimeStatus.packagedConfigPath}` : ""}
                  </Text>
                  <Text type="secondary">
                    模型包 ZIP：{runtimeStatus.modelsPackZipExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.modelsPackZip ? ` · ${runtimeStatus.modelsPackZip}` : ""}
                  </Text>
                  <Text type="secondary">
                    Ollama 包 ZIP：{runtimeStatus.ollamaPackZipExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.ollamaPackZip ? ` · ${runtimeStatus.ollamaPackZip}` : ""}
                  </Text>
                  <Text type="secondary">
                    Ollama EXE：{runtimeStatus.ollamaExeExists ? "已发现" : "未发现"}
                    {devToolsEnabled && runtimeStatus.ollamaExe ? ` · ${runtimeStatus.ollamaExe}` : ""}
                  </Text>
                  {Array.isArray(runtimeStatus.runtimeChecks) &&
                    runtimeStatus.runtimeChecks
                      .filter((item) => item && item.exists === false)
                      .map((item) => (
                        <Text key={String(item?.key || item?.label || item?.path)} type="danger">
                          缺少运行时资源：{item?.label || item?.key || "unknown"}
                          {devToolsEnabled && item?.path ? ` · ${item.path}` : ""}
                        </Text>
                      ))}
                </Space>
              )}
            </Space>
          )}
        </Card>
      </Space>
    </Content>
  );
}

