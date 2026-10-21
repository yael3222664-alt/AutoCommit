import { describe, expect, it, vi } from "vitest";

import {
  computeCropRect,
  cropImage,
  CropError,
  getBoxValidationError,
  isImageReady,
  tryCropImage,
  type CropBox,
  type CropCanvas,
} from "./cropHelper";

function fakeImage(width = 1000, height = 2000, complete = true) {
  return { complete, naturalWidth: width, naturalHeight: height } as unknown as HTMLImageElement;
}

function fakeCanvas(overrides: Partial<CropCanvas> = {}) {
  const drawImage = vi.fn();
  const canvas: CropCanvas = {
    width: 0,
    height: 0,
    getContext: () => ({ drawImage }),
    toDataURL: () => "data:image/png;base64,AAAA",
    ...overrides,
  };
  return { canvas, drawImage };
}

const BOX: CropBox = { ymin: 0.1, xmin: 0.2, ymax: 0.3, xmax: 0.5 };

function expectCropError(fn: () => unknown, code: CropError["code"]) {
  try {
    fn();
  } catch (error) {
    expect(error).toBeInstanceOf(CropError);
    expect((error as CropError).code).toBe(code);
    return;
  }
  throw new Error("expected CropError");
}

describe("getBoxValidationError", () => {
  it("accepts a normal box and the full image", () => {
    expect(getBoxValidationError(BOX)).toBeNull();
    expect(getBoxValidationError({ ymin: 0, xmin: 0, ymax: 1, xmax: 1 })).toBeNull();
  });

  it.each([
    ["missing", null],
    ["negative", { ymin: -0.01, xmin: 0, ymax: 0.5, xmax: 0.5 }],
    ["above one", { ymin: 0, xmin: 0, ymax: 1.0001, xmax: 0.5 }],
    ["NaN", { ymin: Number.NaN, xmin: 0, ymax: 0.5, xmax: 0.5 }],
    ["infinite", { ymin: 0, xmin: 0, ymax: Number.POSITIVE_INFINITY, xmax: 0.5 }],
    ["inverted vertically", { ymin: 0.6, xmin: 0, ymax: 0.5, xmax: 0.5 }],
    ["inverted horizontally", { ymin: 0, xmin: 0.6, ymax: 0.5, xmax: 0.5 }],
    ["zero height", { ymin: 0.5, xmin: 0, ymax: 0.5, xmax: 0.5 }],
    ["zero width", { ymin: 0, xmin: 0.5, ymax: 0.5, xmax: 0.5 }],
  ])("rejects a %s box", (_label, box) => {
    expect(getBoxValidationError(box as CropBox | null)).not.toBeNull();
  });
});

describe("computeCropRect", () => {
  it("maps normalized coordinates to pixels", () => {
    expect(computeCropRect(BOX, 1000, 2000)).toEqual({ x: 200, y: 200, width: 300, height: 400 });
  });

  it("covers the full image at the boundaries", () => {
    expect(computeCropRect({ ymin: 0, xmin: 0, ymax: 1, xmax: 1 }, 640, 480)).toEqual({ x: 0, y: 0, width: 640, height: 480 });
  });

  it("rounds outward so no part of the box is cut off", () => {
    expect(computeCropRect({ ymin: 0.101, xmin: 0.101, ymax: 0.2049, xmax: 0.2049 }, 100, 100)).toEqual({
      x: 10,
      y: 10,
      width: 11,
      height: 11,
    });
  });

  it("keeps a tiny box at least one pixel inside the image", () => {
    expect(computeCropRect({ ymin: 0.9999, xmin: 0.9999, ymax: 1, xmax: 1 }, 10, 10)).toEqual({ x: 9, y: 9, width: 1, height: 1 });
  });

  it("clamps padding to the image edges", () => {
    expect(computeCropRect({ ymin: 0.02, xmin: 0.9, ymax: 0.1, xmax: 0.98 }, 100, 100, 0.05)).toEqual({
      x: 85,
      y: 0,
      width: 15,
      height: 15,
    });
  });

  it("rejects invalid geometry, padding and dimensions", () => {
    expectCropError(() => computeCropRect({ ymin: 0.5, xmin: 0, ymax: 0.4, xmax: 1 }, 100, 100), "invalid_box");
    expectCropError(() => computeCropRect(BOX, 100, 100, -0.1), "invalid_box");
    expectCropError(() => computeCropRect(BOX, 0, 100), "image_not_loaded");
  });
});

describe("cropImage", () => {
  it("draws the box region onto a canvas of matching size", () => {
    const { canvas, drawImage } = fakeCanvas();
    const image = fakeImage();

    const result = cropImage(image, BOX, { createCanvas: () => canvas });

    expect(result).toEqual({
      dataUrl: "data:image/png;base64,AAAA",
      rect: { x: 200, y: 200, width: 300, height: 400 },
      width: 300,
      height: 400,
    });
    expect([canvas.width, canvas.height]).toEqual([300, 400]);
    expect(drawImage).toHaveBeenCalledWith(image, 200, 200, 300, 400, 0, 0, 300, 400);
  });

  it("magnifies the output canvas by the scale factor", () => {
    const { canvas, drawImage } = fakeCanvas();
    const image = fakeImage();

    const result = cropImage(image, BOX, { createCanvas: () => canvas, scale: 1.4 });

    expect(result.rect).toEqual({ x: 200, y: 200, width: 300, height: 400 });
    expect([result.width, result.height]).toEqual([420, 560]);
    expect([canvas.width, canvas.height]).toEqual([420, 560]);
    expect(drawImage).toHaveBeenCalledWith(image, 200, 200, 300, 400, 0, 0, 420, 560);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])("rejects scale %s", (scale) => {
    const { canvas } = fakeCanvas();
    expectCropError(() => cropImage(fakeImage(), BOX, { createCanvas: () => canvas, scale }), "invalid_box");
  });

  it.each([
    [2480, 3508],
    [1240, 1754],
    [3024, 4032],
    [120, 170],
  ])("crops the padded, zoomed field consistently at %ix%i", (width, height) => {
    const box: CropBox = { ymin: 0.2, xmin: 0.3, ymax: 0.25, xmax: 0.6 };
    const { canvas, drawImage } = fakeCanvas();
    const image = fakeImage(width, height);

    const result = cropImage(image, box, { createCanvas: () => canvas, padding: 0.01, scale: 1.4 });

    const { rect } = result;
    expect(rect.x).toBe(Math.floor(0.29 * width + 1e-6));
    expect(rect.y).toBe(Math.floor(0.19 * height + 1e-6));
    expect(rect.x + rect.width).toBe(Math.ceil(0.61 * width - 1e-6));
    expect(rect.y + rect.height).toBe(Math.ceil(0.26 * height - 1e-6));
    expect(rect.x + rect.width).toBeLessThanOrEqual(width);
    expect(rect.y + rect.height).toBeLessThanOrEqual(height);
    expect(result.width).toBe(Math.round(rect.width * 1.4));
    expect(result.height).toBe(Math.round(rect.height * 1.4));
    expect(drawImage).toHaveBeenCalledWith(image, rect.x, rect.y, rect.width, rect.height, 0, 0, result.width, result.height);
  });

  it("clamps padding at the page edge at any resolution", () => {
    for (const [width, height] of [[2480, 3508], [600, 800]] as const) {
      const { canvas } = fakeCanvas();
      const { rect } = cropImage(fakeImage(width, height), { ymin: 0.98, xmin: 0, ymax: 1, xmax: 0.02 }, { createCanvas: () => canvas, padding: 0.05 });
      expect(rect.x).toBe(0);
      expect(rect.y + rect.height).toBe(height);
      expect(rect.x + rect.width).toBe(Math.ceil(0.07 * width - 1e-6));
    }
  });

  it("passes export format options", () => {
    const toDataURL = vi.fn(() => "data:image/jpeg;base64,AAAA");
    const { canvas } = fakeCanvas({ toDataURL });
    cropImage(fakeImage(), BOX, { createCanvas: () => canvas, mimeType: "image/jpeg", quality: 0.8 });
    expect(toDataURL).toHaveBeenCalledWith("image/jpeg", 0.8);
  });

  it.each([
    ["incomplete", fakeImage(1000, 1000, false)],
    ["zero-sized (broken)", fakeImage(0, 0, true)],
  ])("rejects an %s image before touching the canvas", (_label, image) => {
    const createCanvas = vi.fn();
    expectCropError(() => cropImage(image, BOX, { createCanvas }), "image_not_loaded");
    expect(createCanvas).not.toHaveBeenCalled();
  });

  it("rejects an invalid box before touching the canvas", () => {
    const createCanvas = vi.fn();
    expectCropError(() => cropImage(fakeImage(), { ymin: 0.2, xmin: 0.2, ymax: 0.2, xmax: 0.4 }, { createCanvas }), "invalid_box");
    expect(createCanvas).not.toHaveBeenCalled();
  });

  it("reports a missing 2D context", () => {
    const { canvas } = fakeCanvas({ getContext: () => null });
    expectCropError(() => cropImage(fakeImage(), BOX, { createCanvas: () => canvas }), "canvas_unavailable");
  });

  it("reports a missing canvas outside the browser", () => {
    expectCropError(() => cropImage(fakeImage(), BOX), "canvas_unavailable");
  });

  it("reports a tainted canvas for cross-origin images", () => {
    const { canvas } = fakeCanvas({
      toDataURL: () => {
        throw new DOMException("The canvas has been tainted by cross-origin data.", "SecurityError");
      },
    });
    expectCropError(() => cropImage(fakeImage(), BOX, { createCanvas: () => canvas }), "tainted_canvas");
  });

  it.each([
    ["throws", () => { throw new Error("out of memory"); }],
    ["returns an empty data URL", () => "data:,"],
  ])("reports export failure when toDataURL %s", (_label, toDataURL) => {
    const { canvas } = fakeCanvas({ toDataURL });
    expectCropError(() => cropImage(fakeImage(), BOX, { createCanvas: () => canvas }), "export_failed");
  });
});

describe("tryCropImage", () => {
  it("returns the crop on success", () => {
    const { canvas } = fakeCanvas();
    const result = tryCropImage(fakeImage(), BOX, { createCanvas: () => canvas });
    expect(result.ok).toBe(true);
  });

  it("returns the error instead of throwing", () => {
    const missing = tryCropImage(fakeImage(), null);
    const unloaded = tryCropImage(fakeImage(10, 10, false), BOX);
    expect(missing.ok || missing.error.code).toBe("invalid_box");
    expect(unloaded.ok || unloaded.error.code).toBe("image_not_loaded");
  });
});

describe("isImageReady", () => {
  it("requires a completed image with dimensions", () => {
    expect(isImageReady(fakeImage())).toBe(true);
    expect(isImageReady(fakeImage(100, 100, false))).toBe(false);
    expect(isImageReady(fakeImage(0, 100))).toBe(false);
  });
});
