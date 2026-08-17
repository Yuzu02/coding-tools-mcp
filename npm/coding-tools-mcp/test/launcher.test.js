import assert from "node:assert/strict";
import { chmod, copyFile, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const launcher = path.join(packageRoot, "bin", "coding-tools-mcp.js");

async function writeExecutable(directory, name, posixBody, windowsPreload) {
  if (process.platform === "win32") {
    const target = path.join(directory, `${name}.exe`);
    const preload = path.join(directory, `${name}-preload.cjs`);
    await copyFile(process.execPath, target);
    await writeFile(preload, windowsPreload, "utf8");
    return { NODE_OPTIONS: `--require=${preload}` };
  }
  const target = path.join(directory, name);
  await writeFile(target, `#!/bin/sh\n${posixBody}\n`, "utf8");
  await chmod(target, 0o755);
  return {};
}

const windowsArgumentRecorder = [
  'const fs = require("node:fs");',
  'const path = require("node:path");',
  'if (!process.argv[1]?.endsWith("coding-tools-mcp.js")) {',
  "  const args = process.argv.slice(1);",
  "  args[0] = path.basename(args[0]);",
  '  fs.writeFileSync(process.env.RESULT_FILE, `${args.join("\\n")}\\n`);',
  "  process.exit(0);",
  "}",
].join("\n");

function runLauncher(binDirectory, args = [], extraEnv = {}, ambientEnv = process.env) {
  const env = { ...ambientEnv };
  delete env.CODING_TOOLS_MCP_VERSION;
  return spawnSync(process.execPath, [launcher, ...args], {
    encoding: "utf8",
    env: {
      ...env,
      ...extraEnv,
      PATH: binDirectory,
    },
  });
}

test("uvx receives the pinned Python package and forwarded arguments", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "coding-tools-mcp-uvx-"));
  const output = path.join(directory, "args.txt");
  const executableEnv = await writeExecutable(
    directory,
    "uvx",
    'printf "%s\\n" "$@" > "$RESULT_FILE"',
    windowsArgumentRecorder,
  );

  const result = runLauncher(directory, ["--stdio", "--workspace", "/repo"], {
    CODING_TOOLS_MCP_VERSION: "0.2.0",
    RESULT_FILE: output,
    ...executableEnv,
  });

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual((await readFile(output, "utf8")).trim().split("\n"), [
    "coding-tools-mcp==0.2.0",
    "--stdio",
    "--workspace",
    "/repo",
  ]);
});

test("pipx is used when uvx is unavailable", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "coding-tools-mcp-pipx-"));
  const output = path.join(directory, "args.txt");
  const executableEnv = await writeExecutable(
    directory,
    "pipx",
    'printf "%s\\n" "$@" > "$RESULT_FILE"',
    windowsArgumentRecorder,
  );

  const result = runLauncher(
    directory,
    ["--help"],
    { RESULT_FILE: output, ...executableEnv },
    { ...process.env, CODING_TOOLS_MCP_VERSION: "9.9.9" },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual((await readFile(output, "utf8")).trim().split("\n"), [
    "run",
    "coding-tools-mcp",
    "--help",
  ]);
});

test("the launcher explains how to install a supported runner", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "coding-tools-mcp-empty-"));
  const result = runLauncher(directory);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /neither uvx nor pipx was found/);
  assert.match(result.stderr, /pip install coding-tools-mcp/);
});

test("the child exit code is preserved", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "coding-tools-mcp-exit-"));
  const executableEnv = await writeExecutable(
    directory,
    "uvx",
    "exit 7",
    'if (!process.argv[1]?.endsWith("coding-tools-mcp.js")) process.exit(7);',
  );

  const result = runLauncher(directory, [], executableEnv);

  assert.equal(result.status, 7);
});
