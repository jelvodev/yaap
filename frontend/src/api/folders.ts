/**
 * Folders as attachments. The browser packs a dropped or picked folder into one zip with its paths,
 * leaving out `.git` and dependency folders, and uploads it as one attachment of kind folder.
 */
import { zip } from "fflate";

/** The same folders the agent's file tools skip (SKIPPED_FOLDERS in backend/app/agent/tools.py). */
export const SKIPPED_FOLDERS = new Set([".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".next"]);
// The limits of an attachment (backend/app/attachments/files.py): files, size unpacked, and the upload itself.
export const MAX_FOLDER_FILES = 5000;
export const MAX_FOLDER_BYTES = 500 * 1024 * 1024;
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

export interface FolderFile {
  /** Relative to the folder, with / as separator. */
  path: string;
  file: File;
}

export interface PickedFolder {
  name: string;
  files: FolderFile[];
}

const folderZips = new WeakSet<File>();

/** Whether this file is a folder packed by zipFolder, to be uploaded as kind folder. */
export function isFolderZip(file: File): boolean {
  return folderZips.has(file);
}

/** Whether a path lies inside a skipped folder. */
export function isSkipped(path: string): boolean {
  return path.split("/").slice(0, -1).some((part) => SKIPPED_FOLDERS.has(part));
}

function tooMany(name: string): Error {
  return new Error(`${name} has more than ${MAX_FOLDER_FILES} files (without .git and dependency folders). Attach a smaller folder.`);
}

/** Pack the folder's files into one zip named after the folder. */
export async function zipFolder(name: string, files: FolderFile[]): Promise<File> {
  const kept = files.filter((item) => !isSkipped(item.path));
  if (kept.length === 0) throw new Error(`${name} has no files to attach.`);
  if (kept.length > MAX_FOLDER_FILES) throw tooMany(name);
  const total = kept.reduce((sum, item) => sum + item.file.size, 0);
  if (total > MAX_FOLDER_BYTES) throw new Error(`${name} holds more than ${MAX_FOLDER_BYTES / (1024 * 1024)} MB.`);
  const entries: Record<string, Uint8Array> = {};
  for (const item of kept) entries[item.path] = new Uint8Array(await item.file.arrayBuffer());
  const data = await new Promise<Uint8Array>((resolve, reject) =>
    zip(entries, { level: 6 }, (error, result) => (error ? reject(error) : resolve(result))),
  );
  if (data.byteLength > MAX_UPLOAD_BYTES) throw new Error(`${name} packs to more than ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB.`);
  const packed = new File([data as BlobPart], name, { type: "application/zip" });
  folderZips.add(packed);
  return packed;
}

/** The folders picked with a folder input (webkitdirectory), each with its files. */
export function filesFromPicker(list: FileList | File[]): PickedFolder[] {
  const folders = new Map<string, FolderFile[]>();
  for (const file of Array.from(list)) {
    const parts = (file.webkitRelativePath || file.name).split("/");
    const name = parts.length > 1 ? parts[0] : "folder";
    const path = parts.length > 1 ? parts.slice(1).join("/") : parts[0];
    folders.set(name, [...(folders.get(name) ?? []), { path, file }]);
  }
  return [...folders.entries()].map(([name, files]) => ({ name, files }));
}

/** Split a drop into plain files and folders. Must be called while the drop event runs. */
export function droppedItems(transfer: DataTransfer): { files: File[]; folders: FileSystemDirectoryEntry[] } {
  const files: File[] = [];
  const folders: FileSystemDirectoryEntry[] = [];
  const items = Array.from(transfer.items ?? []).filter((item) => item.kind === "file");
  if (items.length === 0) return { files: Array.from(transfer.files), folders };
  for (const item of items) {
    const entry = item.webkitGetAsEntry?.();
    if (entry?.isDirectory) folders.push(entry as FileSystemDirectoryEntry);
    else {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  return { files, folders };
}

function readBatch(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function fileOf(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

/** The files of a dropped folder, without descending into skipped folders. */
export async function readFolderEntry(folder: FileSystemDirectoryEntry): Promise<FolderFile[]> {
  const found: FolderFile[] = [];
  async function walk(directory: FileSystemDirectoryEntry, prefix: string): Promise<void> {
    const reader = directory.createReader();
    // readEntries returns the entries in batches until it returns none.
    for (let batch = await readBatch(reader); batch.length > 0; batch = await readBatch(reader)) {
      for (const entry of batch) {
        const path = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isDirectory) {
          if (!SKIPPED_FOLDERS.has(entry.name)) await walk(entry as FileSystemDirectoryEntry, path);
        } else if (entry.isFile) {
          found.push({ path, file: await fileOf(entry as FileSystemFileEntry) });
          if (found.length > MAX_FOLDER_FILES) throw tooMany(folder.name);
        }
      }
    }
  }
  await walk(folder, "");
  return found;
}
