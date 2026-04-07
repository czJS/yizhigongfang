import React from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  Layout,
  List,
  Row,
  Select,
  Slider,
  Space,
  Steps,
  Switch,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { InboxOutlined, PlayCircleOutlined } from "@ant-design/icons";
import { twoDigitIndex } from "../utils";
import { modeLabel } from "../app/labels";
import { useWizardCtx } from "../app/contexts/WizardContext";
import { dropContainsDirectory, extractDroppedVideoFiles } from "../uploadInputHelpers";

const { Content } = Layout;
const { Text } = Typography;

export function WizardScreen() {
  const {
    wizardStep,
    setWizardStep,
    mode,
    wizardUploading,
    handleAddUpload,
    wizardTasks,
    removeTask,
    moveTask,
    form,
    reviewEnabled,
    setReviewEnabled,
    batchName,
    setBatchName,
    outputDir,
    chooseOutputDir,
    openPath,
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
    applySavedSubtitleSettings,
    createBatchAndGo,
    taskCreationBlocked,
    taskCreationBlockReason,
    canRenewInApp,
    openRenewalModal,
  } = useWizardCtx();
  const modeText = modeLabel(mode);

  // Quality mode: readability + natural dubbing strategies are always enabled by product decision.

  // Quality mode: always enable global rules to keep behavior simple/predictable.
  React.useEffect(() => {
    if (mode !== "quality") return;
    try {
      form.setFieldsValue({ ruleset_disable_global: false });
    } catch {
      // ignore
    }
  }, [mode, form]);

  React.useEffect(() => {
    if (wizardStep !== 1) return;
    const v = form.getFieldValue("ruleset_disable_global");
    if (typeof v !== "boolean") {
      form.setFieldsValue({ ruleset_disable_global: false });
    }
  }, [wizardStep, form]);

  const handleDirectoryDrop = React.useCallback(
    async (event: React.DragEvent<HTMLElement>) => {
      if (wizardUploading || taskCreationBlocked || !dropContainsDirectory(event)) return;
      event.preventDefault();
      event.stopPropagation();
      try {
        const files = await extractDroppedVideoFiles(event);
        if (files.length === 0) {
          message.warning("文件夹中没有可上传的视频文件。");
          return;
        }
        for (const file of files) {
          await handleAddUpload({
            file,
            onSuccess: () => undefined,
            onError: () => undefined,
          } as any);
        }
      } catch (err: any) {
        message.error(err?.message || "读取文件夹失败");
      }
    },
    [handleAddUpload, taskCreationBlocked, wizardUploading]
  );

  return (
    <Content style={{ padding: 24 }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        {taskCreationBlocked ? (
          <Alert
            type="warning"
            showIcon
            message="当前账号处于只读限制"
            description={taskCreationBlockReason}
            action={
              canRenewInApp ? (
                <Button size="small" type="primary" onClick={openRenewalModal}>
                  输入新激活码
                </Button>
              ) : undefined
            }
          />
        ) : null}
        <Card>
          <Steps
            current={wizardStep}
            items={[{ title: "添加素材" }, { title: "交付设置" }, { title: "确认开始" }]}
          />
        </Card>

        {wizardStep === 0 && (
          <Card
            title="Step 1：添加素材"
            extra={<Text type="secondary">最多支持 10 个视频，按上传顺序串行处理</Text>}
          >
            <Upload.Dragger
              multiple
              accept="video/*"
              showUploadList={false}
              disabled={wizardUploading || taskCreationBlocked}
              customRequest={handleAddUpload}
              onDrop={handleDirectoryDrop}
              openFileDialogOnClick={false}
              style={{ marginBottom: 16 }}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">拖拽视频或文件夹</p>
              <p className="ant-upload-hint">支持批量上传和文件夹导入，最多 10 个视频，会按顺序串行处理</p>
            </Upload.Dragger>

            {wizardTasks.length === 0 ? (
              <Alert
                type="info"
                showIcon
                message="还没有文件。请拖拽一个或多个视频文件到上方区域。"
              />
            ) : (
              <List
                bordered
                dataSource={wizardTasks}
                renderItem={(item: any, idx: number) => (
                  <List.Item
                    actions={
                      [
                        <Button key="up" size="small" disabled={idx === 0} onClick={() => moveTask(idx, -1)}>
                          上移
                        </Button>,
                        <Button key="down" size="small" disabled={idx === wizardTasks.length - 1} onClick={() => moveTask(idx, 1)}>
                          下移
                        </Button>,
                        <Button key="rm" danger size="small" onClick={() => removeTask(idx)}>
                          移除
                        </Button>,
                      ]
                    }
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
                <Button type="primary" disabled={wizardTasks.length === 0 || taskCreationBlocked} onClick={() => setWizardStep(1)}>
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
                <Tag color={mode === "quality" ? "purple" : mode === "online" ? "cyan" : "blue"}>{modeText}</Tag>
              </Space>
            }
            extra={
              <Space wrap align="center" size="small">
                <Space size="small" align="center">
                  <Text type="secondary">审核</Text>
                  <Switch checked={reviewEnabled} onChange={(v) => setReviewEnabled(v)} checkedChildren="开" unCheckedChildren="关" />
                </Space>
              </Space>
            }
          >
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Form form={form} layout="vertical">
                <Form.Item label="任务名">
                  <Input
                    value={batchName}
                    onChange={(e) => setBatchName(e.target.value)}
                    placeholder="任务-20251231-1030"
                  />
                </Form.Item>

                <Form.Item label="输出位置" extra="成片与字幕保存位置">
                  <Space.Compact style={{ width: "100%" }}>
                    <Input value={outputDir} readOnly placeholder="点击右侧按钮选择文件夹…" />
                    <Button onClick={chooseOutputDir}>
                      选择文件夹
                    </Button>
                    {outputDir && <Button onClick={() => openPath(outputDir)}>打开</Button>}
                  </Space.Compact>
                </Form.Item>

                <Form.Item label="原片字幕">
                  <Card size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Alert type="info" showIcon message="拖动矩形定位字幕区域（将自动保存并启用擦除）" />

                      <Row gutter={12}>
                        <Col span={24}>
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Row gutter={12}>
                              <Col span={24}>
                                <Text>擦除方式</Text>
                                <Form.Item name="erase_subtitle_method" style={{ marginTop: 8, marginBottom: 0 }}>
                                  <Select
                                    options={[
                                      { label: "智能适配（推荐）", value: "auto" },
                                      { label: "黑带覆盖", value: "fill" },
                                      { label: "柔化覆盖", value: "blur" },
                                      { label: "细节修补", value: "delogo" },
                                    ]}
                                  />
                                </Form.Item>
                              </Col>
                            </Row>
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
                                <Slider
                                  min={0.05}
                                  max={1.0}
                                  step={0.001}
                                  value={regionPickerRect.w}
                                  onChange={(v) => setRegionRectSafe({ w: Number(v) })}
                                />
                              </Col>
                              <Col span={12}>
                                <Text>高度（h）</Text>
                                <Slider
                                  min={0.03}
                                  max={0.6}
                                  step={0.001}
                                  value={regionPickerRect.h}
                                  onChange={(v) => setRegionRectSafe({ h: Number(v) })}
                                />
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
                            style={{ width: "100%", height: "100%", display: "block", objectFit: "contain" }}
                            onLoadedMetadata={() => {
                              setRegionPickerVideoReady(true);
                              setRegionPickerVideoError("");
                              const v = regionPickerVideoRef.current;
                              if (v) {
                                setRegionPickerVideoInfo((prev: any) => ({
                                  ...prev,
                                  duration: Number.isFinite((v as any).duration) ? (v as any).duration : undefined,
                                  w: (v as any).videoWidth || undefined,
                                  h: (v as any).videoHeight || undefined,
                                }));
                              }
                            }}
                            onError={() => {
                              setRegionPickerVideoReady(false);
                              const v = regionPickerVideoRef.current as any;
                              const code = v?.error?.code;
                              const msg = v?.error?.message;
                              setRegionPickerVideoError(
                                `视频加载失败（code=${code || "?"}${msg ? `, ${msg}` : ""}）。可点上方“选择预览视频…”重试。`,
                              );
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

                    </Space>
                  </Card>
                </Form.Item>
              </Form>

              <div style={{ position: "fixed", right: 40, bottom: 40, zIndex: 1000 }}>
                <Space>
                  <Button onClick={() => setWizardStep(0)}>上一步</Button>
                  <Button
                    type="primary"
                    onClick={() => {
                      saveSubtitleSettings({ silent: true });
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
              <Alert type="info" showIcon message="批量任务会按列表顺序串行处理。单个视频失败不会阻塞，系统会自动继续下一个。" />
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label="任务名">{batchName}</Descriptions.Item>
                <Descriptions.Item label="视频数量">{wizardTasks.length}</Descriptions.Item>
                <Descriptions.Item label="输出位置">{outputDir || "未选择（可继续，稍后手动下载交付物）"}</Descriptions.Item>
                <Descriptions.Item label="模式">{modeText}</Descriptions.Item>
              </Descriptions>
              <div style={{ position: "fixed", right: 40, bottom: 40, zIndex: 1000 }}>
                <Space>
                  <Button onClick={() => setWizardStep(1)}>上一步</Button>
                  <Button type="primary" icon={<PlayCircleOutlined />} disabled={taskCreationBlocked} onClick={() => createBatchAndGo(true)}>
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
}

