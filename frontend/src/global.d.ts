export {};

declare global {
  interface Window {
    bridge?: {
      apiBase?: string;
      selectDirectory?: () => Promise<string>;
      getCwd?: () => Promise<string>;
      openPath?: (targetPath: string) => Promise<{ ok: boolean; error?: string }>;
      ensureDir?: (baseDir: string, relativeDir: string) => Promise<{ ok: boolean; path: string }>;
      writeFile?: (
        baseDir: string,
        relativePath: string,
        bytes: Uint8Array,
      ) => Promise<{ ok: boolean; path: string; size: number }>;
    };
  }
}


