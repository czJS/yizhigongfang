import React from "react";
import { Alert, Button, Card, Divider, Input, Modal, Space, Typography, type FormInstance } from "antd";
import { GlossaryModal } from "./modals/GlossaryModal";
import { CleanupArtifactsModal } from "./modals/CleanupArtifactsModal";
import { EraseSubOverrideModal } from "./modals/EraseSubOverrideModal";
import { RegionPickerModal } from "./modals/RegionPickerModal";
import logoUrl from "../assets/miaoyichuhai-logo.png";

const { Paragraph, Text, Title } = Typography;

export function AppOverlays(props: {
  mode: "lite" | "quality" | "online";
  regionPickerFileInputRef: React.RefObject<HTMLInputElement>;
  handleRegionPickerFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;

  glossary: any;

  cleanupDialogOpen: boolean;
  cleanupIncludeDiagnostics: boolean;
  cleanupIncludeResume: boolean;
  cleanupIncludeReview: boolean;
  setCleanupIncludeDiagnostics: (v: boolean) => void;
  setCleanupIncludeResume: (v: boolean) => void;
  setCleanupIncludeReview: (v: boolean) => void;
  confirmCleanupArtifacts: () => Promise<void>;
  setCleanupDialogOpen: (v: boolean) => void;

  overrideModalOpen: boolean;
  overrideEditing: any;
  wizardTasks: any[];
  batchesRef: React.MutableRefObject<any[]>;
  overrideForm: FormInstance;
  setOverrideModalOpen: (v: boolean) => void;
  setOverrideEditing: (v: any) => void;
  applyEraseSubOverrideToWizard: (wizardIdx: number, values: Record<string, any>) => void;
  applyEraseSubOverrideToBatch: (batchId: string, taskIndex: number, values: Record<string, any>) => void;
  openRegionPickerFor: (purpose: "erase" | "subtitle", target: "batch" | "override", localVideoPath: string) => void;
  openRegionPicker: (target: "batch" | "override", localVideoPath: string) => void;
  currentOverrideLocalPath: () => string;

  regionPickerPurpose: "erase" | "subtitle";
  regionPickerOpen: boolean;
  setRegionPickerOpen: (v: boolean) => void;
  onApplyRegionPicker: () => void;
  regionPickerTarget: "batch" | "override";
  regionPickerRect: { x: number; y: number; w: number; h: number };
  setRegionRectSafe: (patch: Partial<{ x: number; y: number; w: number; h: number }>) => void;
  regionPickerSampleFontSize: number;
  setFinalSubtitleFontSize: (v: number) => void;
  regionPickerSampleText: string;
  setRegionPickerSampleText: (s: string) => void;
  regionPickerVideoPath: string;
  regionPickerVideoReady: boolean;
  regionPickerVideoError: string;
  regionPickerVideoInfo: any;
  regionPickerVideoRef: React.RefObject<HTMLVideoElement>;
  setRegionPickerVideoReady: (v: boolean) => void;
  setRegionPickerVideoError: (s: string) => void;
  setRegionPickerVideoInfo: (v: any) => void;
  regionPickerVideoBox: any;
  regionPickerVideoScale: number;
  resetRegionPickerVideo: () => void;

  showAuthGate: boolean;
  authStage: "login" | "activate" | "ready";
  authApiBase: string;
  authEmail: string;
  setAuthEmail: (s: string) => void;
  authCode: string;
  setAuthCode: (s: string) => void;
  authActivationCode: string;
  setAuthActivationCode: (s: string) => void;
  authStatusText: string;
  authError: string;
  devCodeHint: string;
  authUserEmail: string;
  authLicenseStatus: string;
  authLicenseExpireAt: string;
  authDeviceLimit: number;
  authActiveDeviceCount: number;
  authSendingCode: boolean;
  authSendCodeCooldownSeconds: number;
  authLoggingIn: boolean;
  authRedeemingCode: boolean;
  authLicenseLoading: boolean;
  handleAuthSendCode: () => Promise<void>;
  handleAuthLogin: () => Promise<void>;
  handleAuthRedeemCode: () => Promise<void>;
  handleAuthLogout: () => Promise<void>;
  showRenewalModal: boolean;
  canRenewInApp: boolean;
  closeRenewalModal: () => void;

  requireLocalPacks: boolean;
  modelsReady: boolean;
  modelsLoading: boolean;
  modelsZipHint: string;
  missingLabels: string[];
  modelsImporting: boolean;
  handlePickModels: () => Promise<void>;
  modelsRoot: string;
  modelsError: string;
  ollamaReady: boolean;
  ollamaPortOpen: boolean;
  ollamaZipHint: string;
  ollamaImporting: boolean;
  ollamaStarting: boolean;
  handlePickOllama: () => Promise<void>;
  handleEnsureOllama: () => Promise<void>;
  ollamaRoot: string;
  ollamaError: string;
  ollamaLoading: boolean;
  refreshOllamaStatus: () => Promise<void>;
}) {
  return (
    <>
      {props.showAuthGate && (
        <div className="ygf-auth-gate">
          <div className="ygf-auth-shell ygf-auth-shell--quiet">
            <div className="ygf-auth-hero ygf-auth-hero--quiet">
              <div className="ygf-auth-brand">
                <img className="ygf-auth-logo" src={logoUrl} alt="秒译出海 Logo" />
                <Title level={1} className="ygf-auth-title">
                  秒译出海
                </Title>
              </div>
              <Paragraph className="ygf-auth-subtitle">
                先用邮箱验证码确认你的登录身份；如果账号已开通过授权，可直接进入工作台。
                如当前账号尚未开通或需要恢复权限，再输入激活码完成授权。
              </Paragraph>
            </div>

            <Card className="ygf-auth-panel ygf-auth-panel--quiet" bordered={false}>
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <div className="ygf-auth-panel-head">
                  <div>
                    <Title level={3} style={{ margin: 0 }}>
                      登录账号
                    </Title>
                    <Text type="secondary">输入邮箱和验证码即可登录；如需首次开通或恢复权限，可同时填写激活码。</Text>
                  </div>
                </div>

                {!!props.authError && <Alert type="error" showIcon message={props.authError} />}

                <Input
                  size="large"
                  placeholder="请输入登录邮箱"
                  value={props.authEmail}
                  onChange={(e) => props.setAuthEmail(e.target.value)}
                />
                <div className="ygf-auth-inline">
                  <Input
                    size="large"
                    placeholder="请输入邮箱验证码"
                    value={props.authCode}
                    onChange={(e) => props.setAuthCode(e.target.value)}
                    onPressEnter={() => props.handleAuthLogin()}
                  />
                  <Button
                    size="large"
                    onClick={() => props.handleAuthSendCode()}
                    loading={props.authSendingCode}
                    disabled={props.authSendingCode || props.authSendCodeCooldownSeconds > 0}
                  >
                    {props.authSendCodeCooldownSeconds > 0 ? `${props.authSendCodeCooldownSeconds}s后重发` : "发送验证码"}
                  </Button>
                </div>
                <Input
                  size="large"
                  placeholder="请输入激活码（已有授权可留空）"
                  value={props.authActivationCode}
                  onChange={(e) => props.setAuthActivationCode(e.target.value)}
                  onPressEnter={() => props.handleAuthLogin()}
                />
                <Button type="primary" size="large" block loading={props.authLoggingIn} onClick={() => props.handleAuthLogin()}>
                  登录并开始使用
                </Button>

                <Divider style={{ margin: "4px 0" }} />

                <Paragraph className="ygf-auth-footnote">
                  验证码 5 分钟内有效。如未收到验证码，请稍后重试；如需开通、续费或更换授权版本，请联系支持人员处理。
                </Paragraph>
              </Space>
            </Card>
          </div>
        </div>
      )}

      <Modal
        open={props.showRenewalModal && props.canRenewInApp}
        title="输入激活码恢复使用"
        onCancel={props.closeRenewalModal}
        footer={null}
        destroyOnHidden={false}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Text type="secondary">当前账号暂不可继续处理任务。输入适用于当前安装包的激活码后，可立即恢复使用。</Text>
          {!!props.authError && <Alert type="error" showIcon message={props.authError} />}
          <Input
            size="large"
            placeholder="请输入激活码"
            value={props.authActivationCode}
            onChange={(e) => props.setAuthActivationCode(e.target.value)}
            onPressEnter={() => props.handleAuthRedeemCode()}
          />
          <Space style={{ justifyContent: "flex-end", width: "100%" }}>
            <Button onClick={props.closeRenewalModal}>稍后再说</Button>
            <Button type="primary" loading={props.authRedeemingCode || props.authLicenseLoading} onClick={() => props.handleAuthRedeemCode()}>
              确认恢复
            </Button>
          </Space>
        </Space>
      </Modal>

      <input
        ref={props.regionPickerFileInputRef}
        type="file"
        accept="video/*"
        style={{ display: "none" }}
        onChange={props.handleRegionPickerFileChange}
      />

      <GlossaryModal
        open={props.glossary.open}
        loading={props.glossary.loading}
        error={props.glossary.error}
        items={props.glossary.items}
        onClose={props.glossary.closeModal}
        onReload={props.glossary.openModal}
        onDownload={props.glossary.downloadJson}
        onFillExample={props.glossary.fillExample}
        onSave={props.glossary.save}
        onUpdateRow={props.glossary.updateRow}
        onRemoveRow={props.glossary.removeRow}
        onAddRow={props.glossary.addRow}
      />

      <CleanupArtifactsModal
        open={props.cleanupDialogOpen}
        includeDiagnostics={props.cleanupIncludeDiagnostics}
        includeResume={props.cleanupIncludeResume}
        includeReview={props.cleanupIncludeReview}
        setIncludeDiagnostics={props.setCleanupIncludeDiagnostics}
        setIncludeResume={props.setCleanupIncludeResume}
        setIncludeReview={props.setCleanupIncludeReview}
        onConfirm={props.confirmCleanupArtifacts}
        onCancel={() => props.setCleanupDialogOpen(false)}
      />

      <EraseSubOverrideModal
        open={props.overrideModalOpen}
        title={
          props.overrideEditing?.kind === "wizard"
            ? `单个设置：${props.wizardTasks[props.overrideEditing.wizardIdx]?.inputName || ""}`
            : props.overrideEditing?.kind === "batch"
              ? `单个设置：${props.batchesRef.current.find((x) => x.id === props.overrideEditing.batchId)?.tasks?.[props.overrideEditing.taskIndex]?.inputName || ""}`
              : "单个设置"
        }
        overrideForm={props.overrideForm}
        onCancel={() => {
          props.setOverrideModalOpen(false);
          props.setOverrideEditing(null);
        }}
        onClear={() => {
          if (!props.overrideEditing) return;
          if (props.overrideEditing.kind === "wizard") {
            props.applyEraseSubOverrideToWizard(props.overrideEditing.wizardIdx, {});
          } else {
            props.applyEraseSubOverrideToBatch(props.overrideEditing.batchId, props.overrideEditing.taskIndex, {});
          }
          props.setOverrideModalOpen(false);
          props.setOverrideEditing(null);
        }}
        onSave={async () => {
          try {
            const vals = await props.overrideForm.validateFields();
            if (!props.overrideEditing) return;
            if (props.overrideEditing.kind === "wizard") {
              props.applyEraseSubOverrideToWizard(props.overrideEditing.wizardIdx, vals);
            } else {
              props.applyEraseSubOverrideToBatch(props.overrideEditing.batchId, props.overrideEditing.taskIndex, vals);
            }
            props.setOverrideModalOpen(false);
            props.setOverrideEditing(null);
          } catch {
            // ignore
          }
        }}
        onSetRecommendedDefaults={() =>
          props.overrideForm.setFieldsValue({
            sub_font_size: 34,
            sub_margin_v: 24,
            sub_outline: 1,
            sub_alignment: 2,
            sub_place_enable: false,
          })
        }
        onOpenSubtitleRectPicker={() => props.openRegionPickerFor("subtitle", "override", props.currentOverrideLocalPath())}
        onOpenErasePicker={() => props.openRegionPicker("override", props.currentOverrideLocalPath())}
      />

      <RegionPickerModal
        title={props.regionPickerPurpose === "subtitle" ? "字幕位置矩形：可视化定位（拖动进度条定位）" : "硬字幕擦除区域：可视化定位（拖动进度条定位）"}
        open={props.regionPickerOpen}
        onCancel={() => props.setRegionPickerOpen(false)}
        onApply={props.onApplyRegionPicker}
        onPickVideo={() => props.regionPickerFileInputRef.current?.click()}
        onClearVideo={props.resetRegionPickerVideo}
        videoPath={props.regionPickerVideoPath}
        videoReady={props.regionPickerVideoReady}
        videoError={props.regionPickerVideoError}
        videoInfo={props.regionPickerVideoInfo}
        videoRef={props.regionPickerVideoRef}
        setVideoReady={props.setRegionPickerVideoReady}
        setVideoError={props.setRegionPickerVideoError}
        setVideoInfo={props.setRegionPickerVideoInfo}
        rect={props.regionPickerRect}
        setRectSafe={props.setRegionRectSafe}
        videoBox={props.regionPickerVideoBox}
        videoScale={props.regionPickerVideoScale}
        sampleFontSize={props.regionPickerSampleFontSize}
        onChangeSampleFontSize={props.setFinalSubtitleFontSize}
        sampleText={props.regionPickerSampleText}
        onChangeSampleText={props.setRegionPickerSampleText}
      />

      <Modal open={props.requireLocalPacks && !props.modelsReady && !props.modelsLoading} title="模型未就绪" closable={false} maskClosable={false} keyboard={false} footer={null}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>未检测到本地模型。请手动选择并导入模型包（models_pack.zip）。</Text>
          {!!props.modelsZipHint && <Text type="secondary">已发现模型包：{props.modelsZipHint}</Text>}
          {!props.modelsReady && props.missingLabels.length > 0 && <Text type="danger">缺失：{props.missingLabels.join("、")}</Text>}
          <Space>
            <Button type="primary" loading={props.modelsImporting} onClick={() => props.handlePickModels()}>
              选择模型包并导入
            </Button>
          </Space>
          {!!props.modelsRoot && <Text type="secondary">模型目录：{props.modelsRoot}</Text>}
          {!!props.modelsError && <Text type="danger">{props.modelsError}</Text>}
        </Space>
      </Modal>

      <Modal
        open={props.mode === "quality" && props.requireLocalPacks && props.modelsReady && !props.ollamaPortOpen && !props.ollamaLoading}
        title="本地 LLM 未就绪"
        closable={false}
        maskClosable={false}
        keyboard={false}
        footer={null}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>质量模式依赖本地 Ollama。请先导入或启动本地 LLM 服务。</Text>
          <Text type="secondary">安装状态：{props.ollamaReady ? "已安装" : "未安装"}；运行状态：{props.ollamaPortOpen ? "已启动" : "未启动"}</Text>
          {!!props.ollamaZipHint && <Text type="secondary">已发现 Ollama 包：{props.ollamaZipHint}</Text>}
          <Space>
            <Button type="primary" loading={props.ollamaImporting} onClick={() => props.handlePickOllama()}>
              导入 Ollama 包
            </Button>
            <Button loading={props.ollamaStarting} onClick={() => props.handleEnsureOllama()}>
              启动 Ollama
            </Button>
            <Button loading={props.ollamaLoading} onClick={() => props.refreshOllamaStatus()}>
              刷新状态
            </Button>
          </Space>
          {!!props.ollamaRoot && <Text type="secondary">Ollama 目录：{props.ollamaRoot}</Text>}
          {!!props.ollamaError && <Text type="danger">{props.ollamaError}</Text>}
        </Space>
      </Modal>
    </>
  );
}

