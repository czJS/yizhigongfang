import { describe, expect, it } from "vitest";
import { dropContainsDirectory, extractDroppedVideoFiles, getUploadDisplayName, isSupportedVideoFile } from "./uploadInputHelpers";

function createFileEntry(file: File) {
  return {
    isFile: true,
    isDirectory: false,
    name: file.name,
    file: (resolve: (value: File) => void) => resolve(file),
  };
}

function createDirectoryEntry(name: string, children: any[]) {
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => {
      let done = false;
      return {
        readEntries: (resolve: (value: any[]) => void) => {
          if (done) {
            resolve([]);
            return;
          }
          done = true;
          resolve(children);
        },
      };
    },
  };
}

describe("uploadInputHelpers", () => {
  it("recognizes supported video files by mime or extension", () => {
    expect(isSupportedVideoFile(new File(["a"], "demo.mp4", { type: "video/mp4" }))).toBe(true);
    expect(isSupportedVideoFile(new File(["a"], "demo.mov", { type: "" }))).toBe(true);
    expect(isSupportedVideoFile(new File(["a"], "demo.txt", { type: "text/plain" }))).toBe(false);
  });

  it("prefers custom relative display names when present", () => {
    const file = new File(["a"], "demo.mp4", { type: "video/mp4" }) as File & { __ygfDisplayName?: string };
    file.__ygfDisplayName = "clips/demo.mp4";
    expect(getUploadDisplayName(file)).toBe("clips/demo.mp4");
  });

  it("detects dropped directories from dataTransfer entries", () => {
    const event = {
      dataTransfer: {
        items: [
          {
            webkitGetAsEntry: () => ({
              isDirectory: true,
            }),
          },
        ],
      },
    } as any;
    expect(dropContainsDirectory(event)).toBe(true);
  });

  it("extracts nested video files from dropped directories", async () => {
    const first = new File(["a"], "a.mp4", { type: "video/mp4" });
    const second = new File(["b"], "b.mov", { type: "" });
    const ignored = new File(["c"], "c.txt", { type: "text/plain" });
    const event = {
      dataTransfer: {
        items: [
          {
            webkitGetAsEntry: () =>
              createDirectoryEntry("clips", [
                createFileEntry(first),
                createDirectoryEntry("nested", [createFileEntry(second), createFileEntry(ignored)]),
              ]),
          },
        ],
      },
    } as any;

    const files = await extractDroppedVideoFiles(event);

    expect(files).toHaveLength(2);
    expect(getUploadDisplayName(files[0] as any)).toBe("clips/a.mp4");
    expect(getUploadDisplayName(files[1] as any)).toBe("clips/nested/b.mov");
  });
});
