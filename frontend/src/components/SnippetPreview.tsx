import { useMemo } from "react";

import { useImage } from "../hooks/useImage";
import { CONFIDENCE_LABELS } from "../review/groundedFields";
import type { BoundingBox, ConfidenceLevel } from "../types/record";
import { getBoxValidationError, tryCropImage, type CropErrorCode } from "../utils/cropHelper";

export const NO_LOCATION_TEXT = "אין מיקום חזותי זמין";
export const DEFAULT_SNIPPET_ZOOM = 1.4;
export const DEFAULT_SNIPPET_PADDING = 0.01;

const CROP_ERROR_TEXT: Readonly<Record<CropErrorCode, string>> = {
  invalid_box: "המיקום החזותי אינו תקין",
  image_not_loaded: "תמונת העמוד עדיין לא נטענה",
  canvas_unavailable: "הדפדפן אינו תומך בתצוגה המקדימה",
  tainted_canvas: "לא ניתן לחתוך תמונה ממקור חיצוני",
  export_failed: "יצירת התצוגה המקדימה נכשלה",
};

export interface SnippetPreviewProps {
  /** URL of the page image the box is normalized to. */
  src: string | null;
  box: BoundingBox | null;
  label?: string | null;
  /** Output magnification relative to the source image pixels. */
  zoom?: number;
  /** Normalized padding added around the box on every side. */
  padding?: number;
  /** What the extraction read for this field, before any manual edit. */
  reading?: string | null;
  /** Conflicting readings the extraction could not choose between. */
  candidates?: string[];
  confidence?: ConfidenceLevel | null;
}

/** Magnified crop of one field, drawn with an offscreen canvas from the page image. */
export function SnippetPreview({
  src,
  box,
  label = null,
  zoom = DEFAULT_SNIPPET_ZOOM,
  padding = DEFAULT_SNIPPET_PADDING,
  reading = null,
  candidates = [],
  confidence = null,
}: SnippetPreviewProps) {
  const boxError = box ? getBoxValidationError(box) : null;
  const canCrop = box !== null && boxError === null && src !== null;
  const loaded = useImage(canCrop ? src : null);

  const crop = useMemo(
    () => (box && loaded.status === "loaded" ? tryCropImage(loaded.image, box, { padding, scale: zoom }) : null),
    [box, loaded, padding, zoom],
  );

  let body;
  if (!box) body = <p className="snippet-message">{NO_LOCATION_TEXT}</p>;
  else if (boxError) body = <p className="snippet-message">{CROP_ERROR_TEXT.invalid_box}</p>;
  else if (!src) body = <p className="snippet-message">תמונת העמוד אינה זמינה</p>;
  else if (loaded.status === "error") body = <p className="snippet-message" role="alert">לא ניתן לטעון את תמונת העמוד</p>;
  else if (!crop) body = <p className="snippet-message">טוען תצוגה מקדימה…</p>;
  else if (!crop.ok) body = <p className="snippet-message" role="alert">{CROP_ERROR_TEXT[crop.error.code]}</p>;
  else body = <img className="snippet-image" src={crop.dataUrl} width={crop.width} height={crop.height} alt={label ?? ""} />;

  return (
    <section className="card snippet-preview" aria-live="polite">
      <header className="card-header snippet-header">
        <span className="snippet-title">
          תצוגה מוגדלת
          <span className="snippet-zoom">חיתוך ×{zoom}</span>
        </span>
        {label && <span className="snippet-label">{label}</span>}
      </header>
      <div className="snippet-body">{body}</div>
      {label && (
        <footer className="snippet-meta">
          <span className="snippet-reading">
            המנוע קרא: <bdi>{reading ?? "—"}</bdi>
          </span>
          {confidence && <span className={`badge confidence-badge confidence-${confidence}`}>{CONFIDENCE_LABELS[confidence]}</span>}
          {candidates.length > 0 && (
            <span className="snippet-candidates">
              קריאות אפשריות: {candidates.map((text) => <bdi key={text}>{text}</bdi>)}
            </span>
          )}
        </footer>
      )}
    </section>
  );
}
