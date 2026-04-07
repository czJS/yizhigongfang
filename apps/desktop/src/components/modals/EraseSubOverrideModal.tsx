import React from "react";
import { Alert, Button, Card, Col, Form, InputNumber, Modal, Row, Select, Space, Switch, Typography } from "antd";
import type { FormInstance } from "antd";

const { Text } = Typography;

export function EraseSubOverrideModal(props: {
  open: boolean;
  title: React.ReactNode;
  overrideForm: FormInstance;
  onCancel: () => void;
  onClear: () => void;
  onSave: () => Promise<void>;
  onSetRecommendedDefaults: () => void;
  onOpenSubtitleRectPicker: () => void;
  onOpenErasePicker: () => void;
}) {
  const {
    open,
    title,
    overrideForm,
    onCancel,
    onClear,
    onSave,
    onSetRecommendedDefaults,
    onOpenSubtitleRectPicker,
    onOpenErasePicker,
  } = props;

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onCancel}
      footer={[
        <Button key="clear" onClick={onClear}>
          清除单个设置
        </Button>,
        <Button key="cancel" onClick={onCancel}>
          取消
        </Button>,
        <Button
          key="ok"
          type="primary"
          onClick={() => {
            void onSave();
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
            <Button size="small" onClick={onSetRecommendedDefaults}>
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
              <Button onClick={onOpenSubtitleRectPicker}>可视化选择字幕矩形</Button>
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
          <Button onClick={onOpenErasePicker}>可视化定位（视频+拖拽框）</Button>
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
  );
}

