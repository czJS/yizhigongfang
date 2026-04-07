import { useCallback, useEffect } from "react";
import { message } from "antd";

export function useRegionPickerController(opts: {
  regionPickerActive: boolean;
  regionPickerVideoRef: React.RefObject<HTMLVideoElement | null>;
  regionPickerFrameRef: React.RefObject<HTMLDivElement | null>;
  regionPickerVideoPath: string;
  regionPickerVideoReady: boolean;
  regionPickerVideoInfo: { w?: number; h?: number };
  setRegionPickerVideoScale: (n: number) => void;
  setRegionPickerVideoBox: (b: { w: number; h: number; x: number; y: number }) => void;

  route: string;
  wizardStep: number;
  subtitleSource: "has" | "none";
  regionPickerPurpose: "erase" | "subtitle";
  setRegionPickerPurpose: (p: "erase" | "subtitle") => void;
  regionPickerTarget: "batch" | "override";
  setRegionPickerTarget: (t: "batch" | "override") => void;

  toFileUrl: (p: string) => string;
  setRegionPickerVideoPath: (s: string) => void;
  setRegionPickerVideoReady: (v: boolean) => void;
  setRegionPickerVideoError: (s: string) => void;
  setRegionPickerVideoInfo: (v: any) => void;
}) {
  useEffect(() => {
    if (!opts.regionPickerActive) return;
    const v = opts.regionPickerVideoRef.current;
    if (!v) return;

    const update = () => {
      const vw = v.videoWidth || opts.regionPickerVideoInfo.w || 0;
      const vh = v.videoHeight || opts.regionPickerVideoInfo.h || 0;
      const cw = v.clientWidth || opts.regionPickerFrameRef.current?.clientWidth || 0;
      const ch = v.clientHeight || opts.regionPickerFrameRef.current?.clientHeight || 0;
      if (vw > 0 && vh > 0 && cw > 0 && ch > 0) {
        const aspect = vw / vh;
        const dispW = Math.min(cw, ch * aspect);
        const dispH = Math.min(ch, cw / aspect);
        const offsetX = (cw - dispW) / 2;
        const offsetY = (ch - dispH) / 2;
        const scale = dispW / vw;
        opts.setRegionPickerVideoScale(scale > 0 && Number.isFinite(scale) ? scale : 1);
        opts.setRegionPickerVideoBox({ w: dispW, h: dispH, x: offsetX, y: offsetY });
        return;
      }
      if (cw > 0 && ch > 0) {
        opts.setRegionPickerVideoScale(1);
        opts.setRegionPickerVideoBox({ w: cw, h: ch, x: 0, y: 0 });
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
  }, [
    opts,
    opts.regionPickerActive,
    opts.regionPickerVideoPath,
    opts.regionPickerVideoReady,
    opts.regionPickerVideoInfo.w,
    opts.regionPickerVideoInfo.h,
  ]);

  useEffect(() => {
    if (!(opts.route === "wizard" && opts.wizardStep === 1 && opts.subtitleSource === "has")) return;
    if (opts.regionPickerPurpose !== "erase") opts.setRegionPickerPurpose("erase");
    if (opts.regionPickerTarget !== "batch") opts.setRegionPickerTarget("batch");
  }, [opts]);

  const handleRegionPickerFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      try {
        const localPath = String((f as any)?.path || "");
        const localUrl = localPath ? opts.toFileUrl(localPath) : "";
        const url = localUrl || URL.createObjectURL(f);
        opts.setRegionPickerVideoPath(url);
        opts.setRegionPickerVideoReady(false);
        opts.setRegionPickerVideoError("");
        opts.setRegionPickerVideoInfo({ name: f.name, localPath });
        if (!localUrl && !localPath) {
          message.warning("未获取到本地路径，已使用临时预览 URL。");
        }
        message.success(`已选择预览视频：${f.name}`);
      } catch (err: any) {
        message.error(err?.message || "选择预览视频失败");
      } finally {
        e.target.value = "";
      }
    },
    [opts],
  );

  const resetRegionPickerVideo = useCallback(() => {
    opts.setRegionPickerVideoPath("");
    opts.setRegionPickerVideoReady(false);
    opts.setRegionPickerVideoError("");
    opts.setRegionPickerVideoInfo({});
  }, [opts]);

  return { handleRegionPickerFileChange, resetRegionPickerVideo };
}

