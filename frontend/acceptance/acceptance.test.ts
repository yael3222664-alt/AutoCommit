/**
 * Browser acceptance suite: the real React UI in headless Chrome, served by the Vite dev
 * server, talking to the real backend (deterministic fake vision provider, SQLite file).
 *
 *   npm run test:acceptance
 *
 * Requires the backend virtualenv (see integration/backend.ts, AUTOCOMMIT_PYTHON) and a
 * Chrome/Chromium binary: set CHROME_PATH, or ACCEPTANCE_CDP_URL to drive an already
 * running browser over CDP instead of launching one.
 */
import { execFileSync } from "node:child_process";
import { mkdtemp, readdir, rm, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { chromium, type Browser, type Page } from "playwright-core";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { BoundingBox, RecordListResponse, RecordResponse } from "../src/types/record";
import { freePort, startBackend, writeSamplePdf, type BackendProcess } from "../integration/backend";

const FRONTEND_ROOT = path.resolve(import.meta.dirname, "..");
const FIELD_KEYS = ["first_name", "last_name", "id_number", "received_date", "sender", "central_numbers.0", "commitment_numbers.0"];
const ZOOM = 1.4;
const PADDING = 0.01;

function chromeExecutable(): string | undefined {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    try {
      return execFileSync("which", [name], { encoding: "utf8" }).trim();
    } catch {
      // try the next candidate
    }
  }
  return undefined;
}

const fieldSelector = (key: string) => `#field-${key.replace(".", "\\.")}`;

describe("browser acceptance", () => {
  let workDir: string;
  let pdfPath: string;
  let database: string;
  let backend: BackendProcess;
  let vite: ViteDevServer;
  let appUrl: string;
  let browser: Browser;
  let page: Page;
  let ids: Record<number, string>;
  let documentId: string;
  let boxes: Record<string, BoundingBox | null>;
  const consoleErrors: string[] = [];
  const allowedStatuses = new Set<number>(); // 4xx responses this suite provokes on purpose

  const api = (route: string, init?: RequestInit) => fetch(`${backend.baseUrl}${route}`, init);
  const getRecord = async (id: string) => (await (await api(`/api/records/${id}`)).json()) as RecordResponse;
  const imageBase64 = async (id: string) => Buffer.from(await (await api(`/api/records/${id}/image`)).arrayBuffer()).toString("base64");
  const recordButton = (pageNumber: number) => page.locator(".record-list button", { hasText: `עמוד ${pageNumber} ` });

  async function selectPage(pageNumber: number) {
    await recordButton(pageNumber).first().click();
    await page.waitForFunction((n) => document.querySelector(".review-status")?.textContent?.startsWith(`עמוד ${n} `), pageNumber);
  }

  async function waitForPageImage() {
    await page.waitForFunction(() => {
      const img = document.querySelector<HTMLImageElement>(".document-image");
      return Boolean(img && img.complete && img.naturalWidth > 0);
    });
  }

  async function upload() {
    await page.setInputFiles('input[type="file"]', pdfPath);
    await page.waitForFunction(() => !document.body.innerText.includes("מעלה…") && document.body.innerText.includes("3 רשומות"));
  }

  /** Largest distance between rendered overlay edges and the normalized boxes, as a fraction of the page. */
  async function overlayError(): Promise<{ count: number; maxError: number }> {
    const rects = await page.evaluate(() => {
      const img = document.querySelector<HTMLImageElement>(".document-image")!.getBoundingClientRect();
      return [...document.querySelectorAll<HTMLElement>(".overlay-box")].map((el) => {
        const r = el.getBoundingClientRect();
        return {
          key: el.dataset.field!,
          xmin: (r.left - img.left) / img.width,
          ymin: (r.top - img.top) / img.height,
          xmax: (r.right - img.left) / img.width,
          ymax: (r.bottom - img.top) / img.height,
        };
      });
    });
    let maxError = 0;
    for (const rect of rects) {
      const box = boxes[rect.key]!;
      for (const edge of ["xmin", "ymin", "xmax", "ymax"] as const) maxError = Math.max(maxError, Math.abs(rect[edge] - box[edge]));
    }
    return { count: rects.length, maxError };
  }

  async function startVite() {
    process.env.AUTOCOMMIT_API_URL = backend.baseUrl;
    const port = await freePort();
    vite = await createServer({ root: FRONTEND_ROOT, logLevel: "error", server: { host: "127.0.0.1", port, strictPort: true } });
    await vite.listen();
    appUrl = `http://127.0.0.1:${port}/`;
  }

  beforeAll(async () => {
    workDir = await mkdtemp(path.join(tmpdir(), "autocommit-acceptance-"));
    pdfPath = path.join(workDir, "sample.pdf");
    database = path.join(workDir, "acceptance.db");
    await writeSamplePdf(pdfPath);
    backend = await startBackend({ database });
    await startVite();

    if (process.env.ACCEPTANCE_CDP_URL) {
      browser = await chromium.connectOverCDP(process.env.ACCEPTANCE_CDP_URL);
    } else {
      const executablePath = chromeExecutable();
      // Current Chrome builds dropped the old headless mode that playwright's `headless: true` selects.
      browser = await chromium.launch({
        headless: false,
        args: ["--headless=new"],
        ...(executablePath ? { executablePath } : { channel: "chrome" }),
      });
    }
    const context = browser.contexts()[0] ?? (await browser.newContext());
    page = await context.newPage();
    await page.setViewportSize({ width: 1400, height: 1000 });
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const url = message.location().url;
      const status = /status of (\d{3})/.exec(message.text())?.[1];
      if (url.endsWith("/favicon.ico")) return; // the app ships no favicon
      if (!(status && allowedStatuses.has(Number(status)) && url.includes("/api/"))) consoleErrors.push(`${message.text()} (${url})`);
    });
    page.on("pageerror", (error) => consoleErrors.push(`uncaught: ${error.message}`));
  });

  afterAll(async () => {
    await page?.close();
    await browser?.close();
    await vite?.close();
    await backend?.stop();
    if (workDir) await rm(workDir, { recursive: true, force: true });
  });

  it("uploads a multi-page PDF and stores and serves a 300 DPI image per page", async () => {
    await page.goto(appUrl);
    await page.waitForFunction(() => document.body.innerText.includes("0 רשומות"));
    await upload();
    await page.waitForFunction(() => document.querySelector(".review-status")?.textContent?.startsWith("עמוד 1 "));
    await waitForPageImage();

    const list = (await (await api("/api/records")).json()) as RecordListResponse;
    expect(list.items.map((r) => r.page_number).sort()).toEqual([1, 2, 3]);
    ids = Object.fromEntries(list.items.map((r) => [r.page_number, r.id]));
    documentId = list.items[0]!.document_id;
    expect((await readdir(path.join(workDir, "acceptance-pages", documentId))).sort()).toEqual(["1.png", "2.png", "3.png"]);
    for (const record of list.items) {
      const response = await api(`/api/records/${record.id}/image`);
      const png = Buffer.from(await response.arrayBuffer());
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("image/png");
      expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1250, 1667]);
    }
    const natural = await page.$eval(".document-image", (img) => [(img as HTMLImageElement).naturalWidth, (img as HTMLImageElement).naturalHeight]);
    expect(natural).toEqual([1250, 1667]);
  });

  it("draws one aligned overlay per grounded field at any viewport size", async () => {
    const record = await getRecord(ids[1]!);
    boxes = {
      first_name: record.first_name.box,
      last_name: record.last_name.box,
      id_number: record.id_number.box,
      received_date: record.received_date.box,
      sender: record.sender.box,
      "central_numbers.0": record.central_numbers[0]?.box ?? null,
      "commitment_numbers.0": record.commitment_numbers[0]?.box ?? null,
    };
    const grounded = Object.values(boxes).filter(Boolean).length;
    expect(grounded).toBeGreaterThan(0);
    expect(boxes.last_name).toBeNull(); // the fixture leaves one field without a location

    const wide = await overlayError();
    expect(wide.count).toBe(grounded);
    expect(wide.maxError).toBeLessThan(0.003);
    await page.setViewportSize({ width: 1000, height: 900 });
    expect((await overlayError()).maxError).toBeLessThan(0.004);
    await page.setViewportSize({ width: 1400, height: 1000 });
  });

  it("renders the magnified snippet of the focused field, pixel-identical to an independent crop", async () => {
    for (const key of FIELD_KEYS) {
      await page.focus(fieldSelector(key));
      const box = boxes[key];
      if (!box) {
        await page.waitForSelector(".snippet-message");
        expect(await page.locator(".snippet-message").textContent()).toBe("אין מיקום חזותי זמין");
        continue;
      }
      await page.waitForSelector(".snippet-image");
      const label = await page.locator(`label[for="field-${key}"]`).evaluate((el) => el.firstChild?.textContent ?? "");
      const result = await page.evaluate(
        async ({ box, zoom, pad }) => {
          const src = document.querySelector<HTMLImageElement>(".document-image")!;
          const snippet = document.querySelector<HTMLImageElement>(".snippet-image")!;
          await snippet.decode();
          const W = src.naturalWidth;
          const H = src.naturalHeight;
          const eps = 1e-6;
          const clamp = (v: number) => Math.min(1, Math.max(0, v));
          const x = Math.floor(clamp(box.xmin - pad) * W + eps);
          const y = Math.floor(clamp(box.ymin - pad) * H + eps);
          const w = Math.ceil(clamp(box.xmax + pad) * W - eps) - x;
          const h = Math.ceil(clamp(box.ymax + pad) * H - eps) - y;
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(w * zoom);
          canvas.height = Math.round(h * zoom);
          canvas.getContext("2d")!.drawImage(src, x, y, w, h, 0, 0, canvas.width, canvas.height);
          return {
            expected: [canvas.width, canvas.height],
            actual: [snippet.naturalWidth, snippet.naturalHeight],
            identical: canvas.toDataURL("image/png") === snippet.src,
          };
        },
        { box, zoom: ZOOM, pad: PADDING },
      );
      expect(result.actual, key).toEqual(result.expected);
      expect(result.identical, key).toBe(true);
      expect(await page.locator(".overlay-badge").allTextContents(), key).toEqual([label]);
      expect(await page.locator(".snippet-label").textContent(), key).toBe(label);
    }
  });

  it("focuses the matching input when a box on the page is clicked", async () => {
    for (const key of FIELD_KEYS.filter((k) => boxes[k])) {
      await page.click(`.overlay-box[data-field="${key}"]`);
      expect(await page.evaluate(() => document.activeElement?.id)).toBe(`field-${key}`);
      await page.waitForFunction((k) => {
        const pressed = [...document.querySelectorAll<HTMLElement>('.overlay-box[aria-pressed="true"]')].map((b) => b.dataset.field);
        return pressed.length === 1 && pressed[0] === k;
      }, key);
    }
  });

  it("refuses to approve an incomplete record, saves a correction via PATCH, then approves and locks it", async () => {
    allowedStatuses.add(400);
    await page.getByRole("button", { name: "אישור" }).click();
    await page.waitForSelector('.review-form [role="alert"]');
    expect(await page.locator('.review-form [role="alert"]').textContent()).toContain("last_name_missing");
    expect((await getRecord(ids[1]!)).status).toBe("pending_review");

    await page.fill("#field-last_name", "כהן");
    await page.getByRole("button", { name: "שמירת תיקונים" }).click();
    await page.waitForFunction(() => !document.body.innerText.includes("שומר…") && !document.querySelector('.review-form [role="alert"]'));
    const edited = await getRecord(ids[1]!);
    expect(edited.last_name).toMatchObject({ value: "כהן", confidence: "high" });
    expect(edited.review_reasons).not.toContain("last_name_missing");
    expect(await recordButton(1).textContent()).toContain("כהן");

    await page.getByRole("button", { name: "אישור" }).click();
    await page.waitForFunction(() => document.querySelector(".review-status")?.textContent?.includes("מאושר"));
    expect((await getRecord(ids[1]!)).status).toBe("approved");
    expect(await page.$$eval(".review-form input", (els) => els.every((el) => (el as HTMLInputElement).readOnly))).toBe(true);
    const locked = await api(`/api/records/${ids[1]}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ first_name: "X" }) });
    expect(locked.status).toBe(400);
  });

  it("stays consistent under rapid field and record switching", async () => {
    for (let round = 0; round < 15; round++) {
      for (const key of FIELD_KEYS) await page.focus(fieldSelector(key));
      await page.hover('.overlay-box[data-field="first_name"]');
      await page.hover("#field-id_number");
    }
    for (let round = 0; round < 10; round++) {
      for (const n of [2, 3, 1]) await recordButton(n).click();
    }
    await selectPage(3);
    await waitForPageImage();
    await page.mouse.move(5, 5);
    expect(await page.evaluate(() => ({
      pressed: document.querySelectorAll('.overlay-box[aria-pressed="true"]').length,
      label: document.querySelector(".snippet-label")?.textContent ?? null,
      active: document.querySelectorAll(".form-field.active").length,
    }))).toEqual({ pressed: 0, label: null, active: 0 });

    await page.focus("#field-sender");
    await page.waitForSelector(".snippet-image");
    expect(await page.locator(".snippet-label").textContent()).toBe("גורם שולח");
    expect(await page.locator('.overlay-box[aria-pressed="true"]').getAttribute("data-field")).toBe("sender");
    expect(await page.locator(".review-status").textContent()).toMatch(/^עמוד 3 /);
  });

  it("keeps records, images, edits and approvals across a backend restart", async () => {
    const before = await (await api("/api/records")).json();
    const imageBefore = await imageBase64(ids[1]!);
    await backend.stop();
    backend = await startBackend({ database, port: backend.port });
    expect(await (await api("/api/records")).json()).toEqual(before);
    expect(await imageBase64(ids[1]!)).toBe(imageBefore);

    await page.reload();
    await page.waitForFunction(() => document.body.innerText.includes("3 רשומות"));
    await selectPage(1);
    await waitForPageImage();
    expect(await page.locator(".review-status").textContent()).toContain("מאושר");
    expect(await page.inputValue("#field-last_name")).toBe("כהן");
    expect(await page.locator(".overlay-box").count()).toBe(Object.values(boxes).filter(Boolean).length);
  });

  it("shows an error instead of crashing when a stored page image is missing", async () => {
    allowedStatuses.add(404);
    await unlink(path.join(workDir, "acceptance-pages", documentId, "2.png"));
    await (await page.context().newCDPSession(page)).send("Network.clearBrowserCache");
    await selectPage(2);
    await page.waitForSelector(".document-status[role=alert]");
    await page.focus("#field-first_name");
    await page.waitForSelector(".snippet-message[role=alert]");
    expect(await page.locator(".document-status").textContent()).toBe("לא ניתן לטעון את תמונת העמוד");
    expect(await page.locator(".snippet-message").textContent()).toBe("לא ניתן לטעון את תמונת העמוד");
    expect(await page.locator(".overlay-box").count()).toBe(0);
  });

  it("never serves files outside the image store through the app's proxy", async () => {
    for (const hostile of ["..%2F..%2Fetc%2Fpasswd", "%2e%2e%2f%2e%2e", "does-not-exist", `${ids[1]}%2F..%2F..`]) {
      const response = await fetch(`${appUrl}api/records/${hostile}/image`);
      expect(response.status, hostile).toBe(404);
      expect(await response.text(), hostile).not.toMatch(/root:|\/home\/|\/etc\/|\/tmp\//);
    }
  });

  it("handles records saved before page images existed and links images on re-upload", async () => {
    const legacyDb = path.join(workDir, "legacy.db");
    await backend.stop();
    backend = await startBackend({ database: legacyDb, port: backend.port, pageImages: false });
    await page.reload();
    await page.waitForFunction(() => document.body.innerText.includes("0 רשומות"));
    await upload();
    await page.waitForFunction(() => document.querySelector(".review-status")?.textContent?.startsWith("עמוד 1 "));
    await page.focus("#field-first_name");
    expect(await page.locator(".document-viewer").textContent()).toBe("תמונת העמוד אינה זמינה");
    expect(await page.locator(".snippet-message").textContent()).toBe("תמונת העמוד אינה זמינה");

    await page.fill("#field-last_name", "לוי");
    await page.getByRole("button", { name: "שמירת תיקונים" }).click();
    await page.waitForFunction(() => !document.body.innerText.includes("שומר…"));
    const list = (await (await api("/api/records")).json()) as RecordListResponse;
    const legacyId = list.items.find((r) => r.page_number === 1)!.id;
    const edited = await getRecord(legacyId);
    expect(edited).toMatchObject({ image_path: null, last_name: { value: "לוי" } });

    await backend.stop();
    backend = await startBackend({ database: legacyDb, port: backend.port });
    await page.reload();
    await page.waitForFunction(() => document.body.innerText.includes("3 רשומות"));
    await selectPage(1);
    await upload();
    await waitForPageImage();
    const relinked = await getRecord(legacyId);
    expect({ ...relinked, image_path: null, updated_at: "" }).toEqual({ ...edited, updated_at: "" });
    expect(relinked.image_path).toBe(`${relinked.document_id}/1.png`);
    expect(await page.locator(".overlay-box").count()).toBe(Object.values(boxes).filter(Boolean).length);
    await page.focus("#field-first_name");
    await page.waitForSelector(".snippet-image");
  });

  it("logged no uncaught errors or unexpected console errors", () => {
    expect(consoleErrors).toEqual([]);
  });
});
