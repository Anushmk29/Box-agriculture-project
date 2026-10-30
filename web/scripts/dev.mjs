import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { lstat, mkdir, readlink, rename, symlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectDir = fileURLToPath(new URL("../", import.meta.url));

// Keep Next's standard output path, but put Windows dev writes on the local
// system drive. A junction does not need administrator privileges.
if (process.platform === "win32" && process.env.LOCALAPPDATA) {
  const projectId = createHash("sha256")
    .update(path.resolve(projectDir).toLowerCase())
    .digest("hex")
    .slice(0, 16);
  const cacheDir = path.join(
    process.env.LOCALAPPDATA,
    "nextjs-dev",
    projectId,
  );
  const devDir = path.join(projectDir, ".next", "dev");
  await mkdir(cacheDir, { recursive: true });
  // Node resolves generated server modules from the junction's real location.
  // Link dependencies there too so external packages remain resolvable.
  const dependenciesDir = path.join(cacheDir, "node_modules");
  const dependencies = await lstat(dependenciesDir).catch(error => {
    if (error.code === "ENOENT") return null;
    throw error;
  });
  if (!dependencies) {
    await symlink(path.join(projectDir, "node_modules"), dependenciesDir, "junction");
  }
  await mkdir(path.dirname(devDir), { recursive: true });
  const existing = await lstat(devDir).catch(error => {
    if (error.code === "ENOENT") return null;
    throw error;
  });
  if (existing?.isSymbolicLink()) {
    const target = await readlink(devDir);
    if (path.resolve(target).toLowerCase() !== path.resolve(cacheDir).toLowerCase()) {
      throw new Error(`Unexpected dev cache junction at ${devDir}: ${target}`);
    }
  } else {
    if (existing) {
      // Preserve the old cache; a running server's lock prevents this rename.
      const backupDir = `${devDir}-before-ssd-${Date.now()}`;
      try {
        await rename(devDir, backupDir);
      } catch (error) {
        throw new Error("Stop the existing Next.js dev server before running npm run dev.", { cause: error });
      }
      console.log(`Previous dev cache preserved at ${backupDir}`);
      try {
        await symlink(cacheDir, devDir, "junction");
      } catch (error) {
        await rename(backupDir, devDir);
        throw error;
      }
    } else {
      await symlink(cacheDir, devDir, "junction");
    }
  }
  console.log(`Next.js development cache: ${cacheDir}`);
}

const child = spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url)),
  "dev",
  ...process.argv.slice(2),
], { cwd: projectDir, stdio: "inherit" });

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("error", error => {
  console.error(error);
  process.exitCode = 1;
});
child.on("exit", code => {
  process.exitCode = code ?? 1;
});
