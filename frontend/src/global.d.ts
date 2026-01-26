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
      getDeviceCode?: () => Promise<string>;
      verifyLicense?: (cdkey: string) => Promise<{ ok: boolean; deviceCode?: string }>;

      getModelStatus?: () => Promise<{
        ready: boolean;
        root: string;
        zip?: string;
        zipHint?: string;
        missing?: { key?: string; label?: string; path?: string }[];
      }>;
      pickModelPack?: () => Promise<string>;
      extractModelPack?: (zipPath: string) => Promise<{
        ok: boolean;
        root?: string;
        zip?: string;
        zipHint?: string;
        missing?: { key?: string; label?: string; path?: string }[];
        error?: string;
      }>;

      getOllamaStatus?: () => Promise<{
        ready: boolean;
        root: string;
        modelsRoot: string;
        portOpen: boolean;
        zip?: string;
        zipHint?: string;
      }>;
      pickOllamaPack?: () => Promise<string>;
      extractOllamaPack?: (zipPath: string) => Promise<{
        ok: boolean;
        root?: string;
        modelsRoot?: string;
        zip?: string;
        zipHint?: string;
        portOpen?: boolean;
        error?: string;
      }>;
    };
  }
}


