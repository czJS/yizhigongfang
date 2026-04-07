import type React from "react";

const VIDEO_FILE_EXTENSIONS = new Set([
  ".mp4",
  ".mov",
  ".m4v",
  ".mkv",
  ".avi",
  ".webm",
  ".mpeg",
  ".mpg",
  ".mts",
  ".m2ts",
  ".wmv",
  ".flv",
]);

type BrowserFile = File & {
  webkitRelativePath?: string;
  __ygfDisplayName?: string;
};

function getFileExtension(name: string): string {
  const normalized = String(name || "").trim().toLowerCase();
  const dot = normalized.lastIndexOf(".");
  return dot >= 0 ? normalized.slice(dot) : "";
}

function setDisplayName(file: BrowserFile, displayName: string) {
  if (!displayName || displayName === file.name) return file;
  try {
    Object.defineProperty(file, "__ygfDisplayName", {
      value: displayName,
      configurable: true,
    });
  } catch {
    file.__ygfDisplayName = displayName;
  }
  return file;
}

function dedupeFiles(files: BrowserFile[]): BrowserFile[] {
  const seen = new Set<string>();
  const result: BrowserFile[] = [];
  for (const file of files) {
    const key = [
      String((file as any)?.path || ""),
      String(file.__ygfDisplayName || file.webkitRelativePath || file.name || ""),
      String(file.size || 0),
      String(file.lastModified || 0),
    ].join("::");
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(file);
  }
  return result;
}

function fileFromEntry(entry: any): Promise<BrowserFile> {
  return new Promise((resolve, reject) => {
    try {
      entry.file(resolve, reject);
    } catch (error) {
      reject(error);
    }
  });
}

function readAllDirectoryEntries(reader: any): Promise<any[]> {
  return new Promise((resolve, reject) => {
    const collected: any[] = [];
    const pump = () => {
      try {
        reader.readEntries(
          (entries: any[]) => {
            if (!entries || entries.length === 0) {
              resolve(collected);
              return;
            }
            collected.push(...entries);
            pump();
          },
          reject,
        );
      } catch (error) {
        reject(error);
      }
    };
    pump();
  });
}

async function collectFilesFromEntry(entry: any, prefix: string, bucket: BrowserFile[]) {
  if (!entry) return;
  if (entry.isFile) {
    const file = await fileFromEntry(entry);
    if (!isSupportedVideoFile(file)) return;
    const displayName = prefix ? `${prefix}/${file.name}` : file.name;
    bucket.push(setDisplayName(file, displayName));
    return;
  }
  if (!entry.isDirectory || typeof entry.createReader !== "function") return;
  const nextPrefix = prefix ? `${prefix}/${entry.name}` : String(entry.name || "");
  const reader = entry.createReader();
  const children = await readAllDirectoryEntries(reader);
  for (const child of children) {
    await collectFilesFromEntry(child, nextPrefix, bucket);
  }
}

export function isSupportedVideoFile(file: Pick<File, "name" | "type"> | null | undefined): file is BrowserFile {
  if (!file) return false;
  const mime = String(file.type || "").trim().toLowerCase();
  if (mime.startsWith("video/")) return true;
  return VIDEO_FILE_EXTENSIONS.has(getFileExtension(String(file.name || "")));
}

export function getUploadDisplayName(file: Pick<BrowserFile, "name" | "webkitRelativePath" | "__ygfDisplayName">): string {
  return String(file.__ygfDisplayName || file.webkitRelativePath || file.name || "未命名视频");
}

export function dropContainsDirectory(event: Pick<React.DragEvent, "dataTransfer"> | { dataTransfer?: DataTransfer | null }): boolean {
  const items = Array.from((event.dataTransfer as any)?.items || []);
  return items.some((item: any) => {
    try {
      const entry = item?.webkitGetAsEntry?.();
      return Boolean(entry?.isDirectory);
    } catch {
      return false;
    }
  });
}

export async function extractDroppedVideoFiles(
  event: Pick<React.DragEvent, "dataTransfer"> | { dataTransfer?: DataTransfer | null }
): Promise<BrowserFile[]> {
  const dataTransfer = event.dataTransfer as any;
  if (!dataTransfer) return [];

  const fromEntries: BrowserFile[] = [];
  const items = Array.from(dataTransfer.items || []);
  let sawEntry = false;

  for (const item of items) {
    const entry = item?.webkitGetAsEntry?.();
    if (entry) {
      sawEntry = true;
      await collectFilesFromEntry(entry, "", fromEntries);
      continue;
    }
    const file = item?.getAsFile?.();
    if (isSupportedVideoFile(file)) {
      fromEntries.push(file);
    }
  }

  if (fromEntries.length > 0) {
    return dedupeFiles(fromEntries);
  }

  if (!sawEntry) {
    const rawFiles = Array.from(dataTransfer.files || []).filter(isSupportedVideoFile) as BrowserFile[];
    return dedupeFiles(rawFiles);
  }

  return [];
}
