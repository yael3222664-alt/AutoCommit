import type { BoundingBox } from "../types/record";

export type CropErrorCode =
  | "invalid_box"
  | "image_not_loaded"
  | "canvas_unavailable"
  | "tainted_canvas"
  | "export_failed";

export class CropError extends Error {
  constructor(public readonly code: CropErrorCode, message: string, options: { cause?: unknown } = {}) {
    super(message, { cause: options.cause });
    this.name = "CropError";
  }
}

/** Normalized geometry only; page_number is irrelevant once the page image is chosen. */
export type CropBox = Pick<BoundingBox, "ymin" | "xmin" | "ymax" | "xmax">;

export interface PixelRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Minimal image surface needed for cropping (satisfied by HTMLImageElement). */
export interface CropSource {
  readonly complete: boolean;
  readonly naturalWidth: number;
  readonly naturalHeight: number;
}

interface Context2D {
  drawImage(image: CanvasImageSource, sx: number, sy: number, sw: number, sh: number, dx: number, dy: number, dw: number, dh: number): void;
}

/** Minimal canvas surface (satisfied by HTMLCanvasElement); injectable for tests. */
export interface CropCanvas {
  width: number;
  height: number;
  getContext(type: "2d"): Context2D | null;
  toDataURL(type?: string, quality?: number): string;
}

export interface CropOptions {
  /** Extra margin around the box, in normalized units, clamped to the image. */
  padding?: number;
  mimeType?: string;
  quality?: number;
  createCanvas?: () => CropCanvas;
}

const PIXEL_EPSILON = 1e-6;

export type CropResult = { ok: true; dataUrl: string; rect: PixelRect } | { ok: false; error: CropError };

/**
 * Returns null for a croppable box, otherwise the reason it cannot be cropped.
 * Stricter than the backend (which allows ymin == ymax): a zero-area box has no pixels.
 */
export function getBoxValidationError(box: CropBox | null | undefined): string | null {
  if (!box) return "Bounding box is missing";
  const coords = [box.ymin, box.xmin, box.ymax, box.xmax];
  if (!coords.every((value) => typeof value === "number" && Number.isFinite(value))) {
    return "Bounding box coordinates must be finite numbers";
  }
  if (!coords.every((value) => value >= 0 && value <= 1)) return "Bounding box coordinates must be within [0, 1]";
  if (box.ymin >= box.ymax) return "ymin must be less than ymax";
  if (box.xmin >= box.xmax) return "xmin must be less than xmax";
  if ((box.ymax - box.ymin) * (box.xmax - box.xmin) <= 0) return "Bounding box area must be greater than zero";
  return null;
}

export function isValidCropBox(box: CropBox | null | undefined): box is CropBox {
  return getBoxValidationError(box) === null;
}

export function isImageReady(image: CropSource): boolean {
  return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0;
}

/** Convert a normalized box to an integer pixel rectangle inside a width x height image. */
export function computeCropRect(box: CropBox, imageWidth: number, imageHeight: number, padding = 0): PixelRect {
  const reason = getBoxValidationError(box);
  if (reason) throw new CropError("invalid_box", reason);
  if (!(imageWidth > 0 && imageHeight > 0)) throw new CropError("image_not_loaded", "Image has no dimensions");
  if (!Number.isFinite(padding) || padding < 0) throw new CropError("invalid_box", "Padding must be a non-negative number");

  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  // Round outward, ignoring floating-point noise such as 0.1 + 0.05 = 0.15000000000000002.
  const floor = (value: number) => Math.floor(value + PIXEL_EPSILON);
  const ceil = (value: number) => Math.ceil(value - PIXEL_EPSILON);
  const left = floor(clamp(box.xmin - padding) * imageWidth);
  const top = floor(clamp(box.ymin - padding) * imageHeight);
  const right = ceil(clamp(box.xmax + padding) * imageWidth);
  const bottom = ceil(clamp(box.ymax + padding) * imageHeight);

  const x = Math.min(left, imageWidth - 1);
  const y = Math.min(top, imageHeight - 1);
  return { x, y, width: Math.max(1, right - x), height: Math.max(1, bottom - y) };
}

function defaultCanvas(): CropCanvas {
  if (typeof document === "undefined") throw new CropError("canvas_unavailable", "Canvas is not available");
  return document.createElement("canvas") as unknown as CropCanvas;
}

function isSecurityError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "SecurityError";
}

/**
 * Crop a loaded image to the box and return a data URL.
 * Throws CropError; a cross-origin image without CORS headers yields "tainted_canvas".
 */
export function cropImage(image: CropSource & CanvasImageSource, box: CropBox, options: CropOptions = {}): { dataUrl: string; rect: PixelRect } {
  if (!isImageReady(image)) throw new CropError("image_not_loaded", "Image is not fully loaded");
  const rect = computeCropRect(box, image.naturalWidth, image.naturalHeight, options.padding ?? 0);

  const canvas = (options.createCanvas ?? defaultCanvas)();
  canvas.width = rect.width;
  canvas.height = rect.height;
  const context = canvas.getContext("2d");
  if (!context) throw new CropError("canvas_unavailable", "2D canvas context is not available");

  try {
    context.drawImage(image, rect.x, rect.y, rect.width, rect.height, 0, 0, rect.width, rect.height);
  } catch (cause) {
    throw new CropError("export_failed", "Could not draw image onto canvas", { cause });
  }

  let dataUrl: string;
  try {
    dataUrl = canvas.toDataURL(options.mimeType ?? "image/png", options.quality);
  } catch (cause) {
    if (isSecurityError(cause)) {
      throw new CropError("tainted_canvas", "Image is cross-origin and cannot be exported", { cause });
    }
    throw new CropError("export_failed", "Could not export cropped image", { cause });
  }
  if (!dataUrl.startsWith("data:") || dataUrl === "data:,") {
    throw new CropError("export_failed", "Canvas produced an empty image");
  }
  return { dataUrl, rect };
}

/** Non-throwing variant for UI code: returns the crop or the reason it failed. */
export function tryCropImage(image: CropSource & CanvasImageSource, box: CropBox | null | undefined, options: CropOptions = {}): CropResult {
  try {
    if (!box) throw new CropError("invalid_box", "Bounding box is missing");
    return { ok: true, ...cropImage(image, box, options) };
  } catch (error) {
    if (error instanceof CropError) return { ok: false, error };
    return { ok: false, error: new CropError("export_failed", "Unexpected crop failure", { cause: error }) };
  }
}
