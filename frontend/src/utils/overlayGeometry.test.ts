import { describe, expect, it } from "vitest";

import { computeCropRect, type CropBox } from "./cropHelper";
import { badgePlacement, boxToOverlayRect } from "./overlayGeometry";

const BOX: CropBox = { ymin: 0.125, xmin: 0.25, ymax: 0.375, xmax: 0.75 };

// A4 at 300 DPI (backend render), A4 at 150 DPI, a phone photo, and a tiny thumbnail.
const RESOLUTIONS: Array<[number, number]> = [
  [2480, 3508],
  [1240, 1754],
  [3024, 4032],
  [120, 170],
];

/** Pixel rectangle an overlay with these CSS percentages occupies in a container of the given size. */
function overlayPixels(rect: NonNullable<ReturnType<typeof boxToOverlayRect>>, width: number, height: number) {
  const pct = (value: string) => parseFloat(value) / 100;
  return {
    x: pct(rect.left) * width,
    y: pct(rect.top) * height,
    width: pct(rect.width) * width,
    height: pct(rect.height) * height,
  };
}

describe("boxToOverlayRect", () => {
  it("maps normalized coordinates to percentages independent of resolution", () => {
    expect(boxToOverlayRect(BOX)).toEqual({ top: "12.5%", left: "25%", width: "50%", height: "25%" });
  });

  it.each(RESOLUTIONS)("overlay covers the same pixels the crop takes at %ix%i", (width, height) => {
    const overlay = overlayPixels(boxToOverlayRect(BOX)!, width, height);
    const crop = computeCropRect(BOX, width, height);
    expect(overlay.x).toBeCloseTo(BOX.xmin * width, 6);
    expect(overlay.y).toBeCloseTo(BOX.ymin * height, 6);
    expect(overlay.width).toBeCloseTo((BOX.xmax - BOX.xmin) * width, 6);
    expect(overlay.height).toBeCloseTo((BOX.ymax - BOX.ymin) * height, 6);
    // The crop rounds outward, so it always contains the overlay and is at most one pixel larger per edge.
    expect(crop.x).toBeLessThanOrEqual(overlay.x);
    expect(crop.y).toBeLessThanOrEqual(overlay.y);
    expect(crop.x + crop.width).toBeGreaterThanOrEqual(overlay.x + overlay.width - 1e-6);
    expect(crop.y + crop.height).toBeGreaterThanOrEqual(overlay.y + overlay.height - 1e-6);
    expect(overlay.x - crop.x).toBeLessThan(1);
    expect(crop.x + crop.width - (overlay.x + overlay.width)).toBeLessThan(1);
  });

  it("keeps full-page and edge boxes inside the page", () => {
    expect(boxToOverlayRect({ ymin: 0, xmin: 0, ymax: 1, xmax: 1 })).toEqual({ top: "0%", left: "0%", width: "100%", height: "100%" });
    expect(boxToOverlayRect({ ymin: 0.9, xmin: 0.95, ymax: 1, xmax: 1 })).toEqual({ top: "90%", left: "95%", width: "5%", height: "10%" });
  });

  it("does not leak floating-point noise into CSS", () => {
    expect(boxToOverlayRect({ ymin: 0.1, xmin: 0.1, ymax: 0.3, xmax: 0.7 })).toEqual({ top: "10%", left: "10%", width: "60%", height: "20%" });
  });

  it.each([
    ["missing", null],
    ["inverted", { ymin: 0.5, xmin: 0.1, ymax: 0.4, xmax: 0.2 }],
    ["zero area", { ymin: 0.5, xmin: 0.1, ymax: 0.5, xmax: 0.2 }],
    ["out of range", { ymin: -0.1, xmin: 0.1, ymax: 0.4, xmax: 1.2 }],
    ["NaN", { ymin: Number.NaN, xmin: 0.1, ymax: 0.4, xmax: 0.2 }],
  ])("returns null for a %s box instead of correcting it", (_name, box) => {
    expect(boxToOverlayRect(box as CropBox | null)).toBeNull();
  });
});

describe("badgePlacement", () => {
  it("puts the badge below boxes touching the top edge", () => {
    expect(badgePlacement(BOX)).toBe("above");
    expect(badgePlacement({ ...BOX, ymin: 0.01 })).toBe("below");
  });
});
