import { getBoxValidationError, type CropBox } from "./cropHelper";

export interface OverlayRect {
  top: string;
  left: string;
  width: string;
  height: string;
}

const percent = (fraction: number) => `${Number((fraction * 100).toFixed(4))}%`;

/**
 * CSS position of a normalized box inside a container that has the page image's aspect ratio.
 * Percentages keep the overlay aligned at any rendered size or source resolution.
 * Returns null for a box that fails the same validation as cropping (never corrected).
 */
export function boxToOverlayRect(box: CropBox | null | undefined): OverlayRect | null {
  if (!box || getBoxValidationError(box)) return null;
  return {
    top: percent(box.ymin),
    left: percent(box.xmin),
    width: percent(box.xmax - box.xmin),
    height: percent(box.ymax - box.ymin),
  };
}

/** Badge goes above the box unless the box touches the top edge of the page. */
export function badgePlacement(box: CropBox, threshold = 0.05): "above" | "below" {
  return box.ymin < threshold ? "below" : "above";
}
