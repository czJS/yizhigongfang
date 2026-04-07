import React from "react";
import { Alert, Button, Input, Modal, Space, Table, Typography } from "antd";

const { Text } = Typography;

export function GlossaryModal(props: {
  open: boolean;
  loading: boolean;
  error: string;
  items: any[];
  onClose: () => void;
  onReload: () => void;
  onDownload: () => void;
  onFillExample: () => void;
  onSave: () => void;
  onUpdateRow: (id: string, patch: any) => void;
  onRemoveRow: (id: string) => void;
  onAddRow: () => void;
}) {
  const { open, loading, error, items, onClose, onReload, onDownload, onFillExample, onSave, onUpdateRow, onRemoveRow, onAddRow } =
    props;

  return (
    <Modal
      title="中文纠错表（翻译前生效）"
      open={open}
      onCancel={onClose}
      footer={
        <Space wrap>
          <Button onClick={onReload} disabled={loading}>
            重新加载
          </Button>
          <Button onClick={onDownload} disabled={loading}>
            下载纠错表
          </Button>
          <Button onClick={onFillExample} disabled={loading}>
            填入示例
          </Button>
          <Button type="primary" onClick={onSave} loading={loading}>
            保存纠错表
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Alert type="info" showIcon message="用法：填写“错字（中文）→ 改成（中文）”。保存后对新建任务生效，并在短语识别前应用。" />
        {!!error && <Alert type="warning" showIcon message={error} />}
        <Text type="secondary">提示：这是稳定的“替换纠错”，不会影响英文翻译风格。</Text>
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={items}
          columns={[
            {
              title: "错字（中文）",
              dataIndex: "src",
              render: (v: string, r: any) => (
                <Input value={v} placeholder="例如：一只千年蚊子" onChange={(e) => onUpdateRow(r.id, { src: e.target.value })} />
              ),
            },
            {
              title: "改成（中文）",
              dataIndex: "tgt",
              render: (v: string, r: any) => (
                <Input value={v} placeholder="例如：蚊子" onChange={(e) => onUpdateRow(r.id, { tgt: e.target.value })} />
              ),
            },
            {
              title: "备注（可选）",
              dataIndex: "note",
              render: (v: string, r: any) => (
                <Input value={v} placeholder="备注" onChange={(e) => onUpdateRow(r.id, { note: e.target.value })} />
              ),
            },
            {
              title: "操作",
              dataIndex: "op",
              width: 70,
              render: (_: any, r: any) => (
                <Button size="small" danger onClick={() => onRemoveRow(r.id)}>
                  删除
                </Button>
              ),
            },
          ]}
        />
        <Button onClick={onAddRow}>添加纠错</Button>
      </Space>
    </Modal>
  );
}

