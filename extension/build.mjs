/**
 * Chrome Extension build script.
 *
 * Runs 3 separate Vite builds:
 * 1. Side Panel — HTML entry, regular web page with module scripts
 * 2. Content Script — IIFE format, self-contained (no import/export)
 * 3. Background Service Worker — ES module (supported in Manifest V3)
 *
 * Then copies static assets (manifest.json, icons, CSS).
 *
 * Usage:
 *   node build.mjs          — one-time production build
 *   node build.mjs --watch  — watch mode (rebuild on file changes, no dev server)
 *
 * Note: Always use build mode (not `vite dev`) for Chrome extensions.
 * Dev server injects React Fast Refresh as an inline <script>, which violates
 * Manifest V3 CSP ('script-src self') and gets blocked.
 */

import { build } from "vite";
import react from "@vitejs/plugin-react";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { copyFileSync, mkdirSync, existsSync, rmSync } from "fs";

const watchMode = process.argv.includes("--watch");
const prodMode = process.argv.includes("--prod");
const watchConfig = watchMode ? { watch: {} } : {};

// Vite mode controls which .env.* files are loaded:
//   production  → .env, .env.production, .env.production.local
//   development → .env, .env.development, .env.development.local
// `build:dev` and `node build.mjs --watch` both go into development mode and
// pick up `.env.development` (localhost URLs). `build:prod` stays in
// production mode and falls back to constants.ts prod defaults.
//
// CAVEAT: dist-dev/ now bakes localhost into the bundle. Do NOT zip dist-dev/
// for coworkers / Web Store — they'll silently fail against localhost. Use
// `build:prod` for any artifact that leaves your machine.
const mode = prodMode ? "production" : "development";

// Separate output dirs so dev + prod artifacts coexist (e.g. you can keep a
// dev build loaded in Chrome while zipping the prod build for coworkers).
const outDir = prodMode ? "dist-prod" : "dist-dev";

const __dirname = dirname(fileURLToPath(import.meta.url));
const r = (...args) => resolve(__dirname, ...args);

const sharedResolve = {
  alias: { "@shared": r("src/shared") },
};

// Three Vite builds all write to the same outDir. If any of them has
// `emptyOutDir: true`, a rebuild in watch mode will nuke the other two's
// outputs (sidepanel would wipe content.js + background.js + manifest.json).
// Clean outDir once up front here, and keep `emptyOutDir: false` everywhere.
rmSync(r(outDir), { recursive: true, force: true });
mkdirSync(r(outDir), { recursive: true });

// Build 1: Side Panel (HTML entry)
console.log("\n--- Building Side Panel ---");
await build({
  configFile: false,
  root: __dirname,
  mode,
  plugins: [react()],
  resolve: sharedResolve,
  build: {
    outDir,
    emptyOutDir: false,
    rollupOptions: {
      input: {
        sidepanel: r("src/sidepanel/index.html"),
        blank: r("src/sidepanel/blank.html"),
      },
    },
    ...watchConfig,
  },
});

// Build 2: Content Script (IIFE, self-contained, no imports)
console.log("\n--- Building Content Script ---");
await build({
  configFile: false,
  root: __dirname,
  mode,
  resolve: sharedResolve,
  build: {
    outDir,
    emptyOutDir: false,
    lib: {
      entry: r("src/content/index.ts"),
      formats: ["iife"],
      name: "KpaxBallContent",
      fileName: () => "content.js",
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
    ...watchConfig,
  },
});

// Build 2b: Page bridge (MAIN-world script, IIFE, no imports). Loaded into
// the page by the content script via chrome.runtime.getURL("page-bridge.js")
// so that it runs in the page's main world and can read window.ethereum.
console.log("\n--- Building Page Bridge ---");
await build({
  configFile: false,
  root: __dirname,
  mode,
  resolve: sharedResolve,
  build: {
    outDir,
    emptyOutDir: false,
    lib: {
      entry: r("src/content/page-bridge.ts"),
      formats: ["iife"],
      name: "KpaxPageBridge",
      fileName: () => "page-bridge.js",
    },
    rollupOptions: {
      output: { inlineDynamicImports: true },
    },
    ...watchConfig,
  },
});

// Build 3: Background Service Worker (ES module)
console.log("\n--- Building Background ---");
await build({
  configFile: false,
  root: __dirname,
  mode,
  resolve: sharedResolve,
  build: {
    outDir,
    emptyOutDir: false,
    rollupOptions: {
      input: { background: r("src/background/index.ts") },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "assets/[name]-[hash].js",
      },
    },
    ...watchConfig,
  },
});

// Copy static assets (manifest.json, icons, content CSS).
function copyStaticAssets() {
  const manifestSrc = prodMode ? r("manifest.prod.json") : r("manifest.json");
  copyFileSync(manifestSrc, r(`${outDir}/manifest.json`));

  const iconsDir = r(`${outDir}/icons`);
  if (!existsSync(iconsDir)) mkdirSync(iconsDir, { recursive: true });
  for (const size of ["16", "48", "128"]) {
    const src = r(`public/icons/icon-${size}.png`);
    if (existsSync(src)) {
      copyFileSync(src, resolve(iconsDir, `icon-${size}.png`));
    }
  }

  const cssSrc = r("src/content/styles.css");
  if (existsSync(cssSrc)) {
    copyFileSync(cssSrc, r(`${outDir}/content.css`));
  }

  for (const name of ["cn-guide.jpg", "en-guide.jpg"]) {
    const src = r(`public/${name}`);
    if (existsSync(src)) copyFileSync(src, r(`${outDir}/${name}`));
  }
}

console.log("\n--- Copying static assets ---");
console.log(`  Using ${prodMode ? "manifest.prod.json (PROD)" : "manifest.json (DEV)"}`);
copyStaticAssets();

if (watchMode) {
  // Re-copy when manifest.json / content styles.css are edited.
  import("fs").then(({ watch }) => {
    const manifestSrc = prodMode ? r("manifest.prod.json") : r("manifest.json");
    watch(manifestSrc, () => copyStaticAssets());
    const cssSrc = r("src/content/styles.css");
    if (existsSync(cssSrc)) watch(cssSrc, () => copyStaticAssets());
  });

  console.log(`\n✓ Watch mode active — ${outDir}/ will rebuild on file changes.`);
  console.log("  After each rebuild, click 🔄 in chrome://extensions to reload.\n");
} else {
  console.log(`\n✓ Build complete! Load ${outDir}/ in chrome://extensions`);
}
