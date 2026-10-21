import { useEffect, useState } from "react";

export type ImageLoadState =
  | { status: "idle"; image: null }
  | { status: "loading"; image: null }
  | { status: "loaded"; image: HTMLImageElement }
  | { status: "error"; image: null };

interface Tracked {
  src: string | null;
  state: ImageLoadState;
}

/** Loads an image off-DOM. CORS mode keeps canvases drawn from it exportable. */
export function useImage(src: string | null): ImageLoadState {
  const [tracked, setTracked] = useState<Tracked>({ src: null, state: { status: "idle", image: null } });

  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.decoding = "async";
    image.onload = () => {
      if (!cancelled) setTracked({ src, state: { status: "loaded", image } });
    };
    image.onerror = () => {
      if (!cancelled) setTracked({ src, state: { status: "error", image: null } });
    };
    image.src = src;
    return () => {
      cancelled = true;
      image.onload = null;
      image.onerror = null;
    };
  }, [src]);

  if (!src) return { status: "idle", image: null };
  return tracked.src === src ? tracked.state : { status: "loading", image: null };
}
