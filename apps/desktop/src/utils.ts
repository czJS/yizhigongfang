export function nowTs() {
  return Date.now();
}

export function defaultBatchName(ts = Date.now()): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `批次-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

export function safeStem(name: string): string {
  // remove extension
  const base = name.replace(/\.[^/.]+$/, "");
  // keep chinese/english/digits/underscore/dash, replace others with underscore
  const cleaned = base.replace(/[^\u4e00-\u9fa5a-zA-Z0-9_-]+/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "");
  return cleaned || "未命名";
}

export function twoDigitIndex(n: number): string {
  return String(n).padStart(3, "0");
}

export function prettySize(bytes: number) {
  if (!bytes && bytes !== 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}


