import React from "react";
import { Checkbox, Modal, Space, Typography } from "antd";

const { Text } = Typography;

export function CleanupArtifactsModal(props: {
  open: boolean;
  includeDiagnostics: boolean;
  includeResume: boolean;
  includeReview: boolean;
  setIncludeDiagnostics: (v: boolean) => void;
  setIncludeResume: (v: boolean) => void;
  setIncludeReview: (v: boolean) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const {
    open,
    includeDiagnostics,
    includeResume,
    includeReview,
    setIncludeDiagnostics,
    setIncludeResume,
    setIncludeReview,
    onCancel,
    onConfirm,
  } = props;

  return (
    <Modal
      title="清理中间产物"
      open={open}
      okText="开始清理"
      cancelText="取消"
      okButtonProps={{
        danger: true,
        disabled: !includeDiagnostics && !includeResume && !includeReview,
      }}
      onOk={onConfirm}
      onCancel={onCancel}
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Text type="secondary">仅对当前任务生效，不会删除交付物。</Text>
        <Checkbox checked={includeDiagnostics} onChange={(e) => setIncludeDiagnostics(e.target.checked)}>
          清理诊断/日志文件（建议）
        </Checkbox>
        <Checkbox checked={includeResume} onChange={(e) => setIncludeResume(e.target.checked)}>
          清理断点续跑文件（可能无法继续从上次继续）
        </Checkbox>
        <Checkbox checked={includeReview} onChange={(e) => setIncludeReview(e.target.checked)}>
          清理审校文件（将丢失审校稿）
        </Checkbox>
      </Space>
    </Modal>
  );
}

