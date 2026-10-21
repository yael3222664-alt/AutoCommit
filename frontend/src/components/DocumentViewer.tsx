import { useState } from "react";

import type { GroundedField } from "../review/groundedFields";
import { badgePlacement, boxToOverlayRect } from "../utils/overlayGeometry";

export interface DocumentViewerProps {
  /** URL of the rendered page image; boxes are normalized to it. */
  src: string | null;
  alt: string;
  fields: readonly GroundedField[];
  activeKey: string | null;
  onSelect(key: string): void;
  onHover?(key: string | null): void;
}

type LoadStatus = "loading" | "loaded" | "error";

/** Page image with a clickable overlay for every field that has a valid box on this page. */
export function DocumentViewer({ src, alt, fields, activeKey, onSelect, onHover }: DocumentViewerProps) {
  const [status, setStatus] = useState<{ src: string | null; value: LoadStatus }>({ src, value: "loading" });
  const loadStatus = status.src === src ? status.value : "loading";

  if (!src) return <div className="document-viewer document-viewer-empty">תמונת העמוד אינה זמינה</div>;

  return (
    <div className="document-viewer">
      <div className="document-page">
        <img
          className="document-image"
          src={src}
          alt={alt}
          crossOrigin="anonymous"
          onLoad={() => setStatus({ src, value: "loaded" })}
          onError={() => setStatus({ src, value: "error" })}
        />
        {loadStatus !== "error" &&
          fields.map((field) => {
            const rect = field.box && boxToOverlayRect(field.box);
            if (!field.box || !rect) return null;
            const active = field.key === activeKey;
            return (
              <button
                key={field.key}
                type="button"
                className={`overlay-box confidence-${field.confidence}${active ? " active" : ""}`}
                style={rect}
                aria-label={field.label}
                aria-pressed={active}
                data-field={field.key}
                onClick={() => onSelect(field.key)}
                onMouseEnter={() => onHover?.(field.key)}
                onMouseLeave={() => onHover?.(null)}
              >
                {active && <span className={`overlay-badge ${badgePlacement(field.box)}`}>{field.label}</span>}
              </button>
            );
          })}
      </div>
      {loadStatus === "loading" && <p className="document-status">טוען את תמונת העמוד…</p>}
      {loadStatus === "error" && (
        <p className="document-status" role="alert">
          לא ניתן לטעון את תמונת העמוד
        </p>
      )}
    </div>
  );
}
