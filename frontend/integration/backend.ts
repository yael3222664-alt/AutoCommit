import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Launches backend/tests/integration_server.py: the real API with a deterministic fake vision provider. */

const REPO_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PYTHON = process.env.AUTOCOMMIT_PYTHON ?? path.join(REPO_ROOT, ".venv", "bin", "python");
const SERVER_MODULE = "backend.tests.integration_server";
const STARTUP_TIMEOUT_MS = 30_000;

export interface BackendProcess {
  baseUrl: string;
  port: number;
  stop(): Promise<void>;
}

export interface BackendOptions {
  database: string;
  port?: number;
  failPages?: number[];
}

export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

function collectOutput(child: ChildProcess): () => string {
  let output = "";
  child.stdout?.on("data", (chunk) => (output += String(chunk)));
  child.stderr?.on("data", (chunk) => (output += String(chunk)));
  return () => output;
}

export function writeSamplePdf(destination: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ["-m", SERVER_MODULE, "--write-sample-pdf", destination], { cwd: REPO_ROOT });
    const output = collectOutput(child);
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`Sample PDF generation failed (${code}):\n${output()}`)),
    );
  });
}

function stopProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    child.once("exit", () => resolve());
    child.kill("SIGTERM");
  });
}

export async function startBackend({ database, port, failPages = [] }: BackendOptions): Promise<BackendProcess> {
  const resolvedPort = port ?? (await freePort());
  const args = ["-m", SERVER_MODULE, "--port", String(resolvedPort), "--database", database];
  if (failPages.length > 0) args.push("--fail-pages", ...failPages.map(String));
  const child = spawn(PYTHON, args, { cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] });
  const output = collectOutput(child);
  const baseUrl = `http://127.0.0.1:${resolvedPort}`;
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;

  for (;;) {
    if (child.exitCode !== null) throw new Error(`Backend exited during startup (${child.exitCode}):\n${output()}`);
    try {
      if ((await fetch(`${baseUrl}/api/health`)).ok) break;
    } catch {
      // Not listening yet.
    }
    if (Date.now() > deadline) {
      await stopProcess(child);
      throw new Error(`Backend did not become healthy within ${STARTUP_TIMEOUT_MS} ms:\n${output()}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return { baseUrl, port: resolvedPort, stop: () => stopProcess(child) };
}
