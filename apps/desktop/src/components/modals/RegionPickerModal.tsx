import React from "react";
import { Alert, Button, Col, Input, InputNumber, Modal, Row, Slider, Space, Typography } from "antd";
import { UploadOutlined } from "@ant-design/icons";

const { Text } = Typography;

export function RegionPickerModal(props: {
  open: boolean;
  title: string;
  onCancel: () => void;
  onApply: () => void;
  onPickVideo: () => void;
  onClearVideo: () => void;

  videoPath: string;
  videoReady: boolean;
  videoError: string;
  videoInfo: { name?: string; duration?: number; w?: number; h?: number };
  videoRef: React.RefObject<HTMLVideoElement>;
  setVideoReady: (v: boolean) => void;
  setVideoError: (s: string) => void;
  setVideoInfo: React.Dispatch<React.SetStateAction<any>>;

  rect: { x: number; y: number; w: number; h: number };
  setRectSafe: (patch: Partial<{ x: number; y: number; w: number; h: number }>) => void;
  videoBox: { x: number; y: number; w: number; h: number };
  videoScale: number;

  sampleFontSize: number;
  onChangeSampleFontSize: (v: number) => void;
  sampleText: string;
  onChangeSampleText: (s: string) => void;
}) {
  const {
    open,
    title,
    onCancel,
    onApply,
    onPickVideo,
    onClearVideo,
    videoPath,
    videoReady,
    videoError,
    videoInfo,
    videoRef,
    setVideoReady,
    setVideoError,
    setVideoInfo,
    rect,
    setRectSafe,
    videoBox,
    videoScale,
    sampleFontSize,
    onChangeSampleFontSize,
    sampleText,
    onChangeSampleText,
  } = props;

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onCancel}
      onOk={onApply}
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
          <Button icon={<UploadOutlined />} onClick={onPickVideo}>
            选择预览视频…
          </Button>
          <Button onClick={onClearVideo} disabled={!videoPath}>
            清除
          </Button>
          <Text type="secondary">预览视频仅用于定位坐标，不上传后端。</Text>
        </Space>
        <Text type={videoError ? "danger" : videoReady ? "success" : "secondary"}>
          {videoError
            ? `加载失败：${videoError}`
            : videoReady
              ? `已加载：${videoInfo.name || "预览视频"}（时长 ${(videoInfo.duration || 0).toFixed(2)}s，${videoInfo.w || "?"}×${videoInfo.h || "?"}）`
              : videoPath
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
              max={Math.max(0, 1 - rect.h)}
              step={0.001}
              value={rect.y}
              onChange={(v) => setRectSafe({ y: Number(v) })}
            />
            <Row gutter={12}>
              <Col span={12}>
                <Text>区域宽度（w）</Text>
                <Slider min={0.05} max={1.0} step={0.001} value={rect.w} onChange={(v) => setRectSafe({ w: Number(v) })} />
              </Col>
              <Col span={12}>
                <Text>区域高度（h）</Text>
                <Slider min={0.03} max={0.6} step={0.001} value={rect.h} onChange={(v) => setRectSafe({ h: Number(v) })} />
              </Col>
            </Row>
            <Space wrap>
              <Text>最终字幕字号（会影响成片）：</Text>
              <InputNumber
                size="small"
                min={10}
                max={60}
                value={sampleFontSize}
                onChange={(v) => onChangeSampleFontSize(Number(v || 18))}
              />
              <Input
                size="small"
                style={{ width: 280 }}
                value={sampleText}
                onChange={(e) => onChangeSampleText(e.target.value)}
              />
              <Text type="secondary">（示例文字会在矩形中纵向居中，和成片一致）</Text>
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
          {videoPath ? (
            <video
              ref={videoRef}
              src={videoPath}
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
                setVideoReady(true);
                setVideoError("");
                const v = videoRef.current;
                if (v) {
                  setVideoInfo((prev) => ({
                    ...prev,
                    duration: Number.isFinite(v.duration) ? v.duration : undefined,
                    w: v.videoWidth || undefined,
                    h: v.videoHeight || undefined,
                  }));
                }
              }}
              onError={() => {
                setVideoReady(false);
                const v = videoRef.current as any;
                const code = v?.error?.code;
                const msg = v?.error?.message;
                setVideoError(`视频加载失败（code=${code || "?"}${msg ? `, ${msg}` : ""}）。可点上方“选择预览视频…”重试。`);
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
              left: `${videoBox.x + rect.x * videoBox.w}px`,
              top: `${videoBox.y + rect.y * videoBox.h}px`,
              width: `${rect.w * videoBox.w}px`,
              height: `${rect.h * videoBox.h}px`,
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
                fontSize: sampleFontSize * videoScale,
                fontWeight: 400,
                lineHeight: 1.0,
                textAlign: "center",
                textShadow: "0 0 0 rgba(0,0,0,0.6)",
                whiteSpace: "pre-wrap",
                maxWidth: "100%",
              }}
            >
              {sampleText}
            </div>
          </div>
        </div>
      </Space>
      {!!videoPath && (
        <div style={{ marginTop: 10 }}>
          {!videoReady && !videoError && <Text type="secondary">视频加载中…</Text>}
          {!!videoError && <Text type="danger">{videoError}</Text>}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <Text type="secondary">
          当前（ratio）：x={rect.x.toFixed(3)} y={rect.y.toFixed(3)} w={rect.w.toFixed(3)} h={rect.h.toFixed(3)}
        </Text>
      </div>
    </Modal>
  );
}

