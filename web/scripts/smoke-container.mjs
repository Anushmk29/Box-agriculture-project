import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("..", import.meta.url));
const name = `box-web-smoke-${process.pid}`;
const tag = "box-web-smoke:local";
const docker = args => execFileSync("docker", args, { cwd: root, encoding: "utf8" });
docker(["build", "-t", tag, "."]);
try {
  docker(["run", "-d", "--name", name, "-p", "127.0.0.1::3000", tag]);
  const binding = docker(["port", name, "3000/tcp"]).trim();
  const expected = readFileSync(new URL("../public/logo.png", import.meta.url));
  let response;
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      response = await fetch(`http://${binding}/logo.png`);
      if (response.ok) break;
    } catch { /* Server startup: retry within a fixed deadline. */ }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  assert.equal(response?.status, 200, "Container must serve /logo.png");
  assert.match(response.headers.get("content-type"), /image\/png/);
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), expected);
  console.log("Container smoke passed: /logo.png matches the public asset.");
} finally {
  docker(["rm", "-f", name]);
}
