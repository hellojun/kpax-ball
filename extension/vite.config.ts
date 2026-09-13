import { resolve } from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { copyFileSync, mkdirSync, existsSync } from "fs";

function ensureDir(dir: string) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: "copy-extension-assets",
      writeBundle() {
        const dist = resolve(__dirname, "dist");
        ensureDir(dist);

        // Copy manifest.json
        copyFileSync(
          resolve(__dirname, "manifest.json"),
          resolve(dist, "manifest.json")
        );

        // Copy icons
        const iconsOut = resolve(dist, "icons");
        ensureDir(iconsOut);
        for (const size of ["16", "48", "128"]) {
          const src = resolve(__dirname, `public/icons/icon-${size}.png`);
          if (existsSync(src)) {
            copyFileSync(src, resolve(iconsOut, `icon-${size}.png`));
          }
        }

        // Copy content script CSS
        const cssSrc = resolve(__dirname, "src/content/styles.css");
        if (existsSync(cssSrc)) {
          copyFileSync(cssSrc, resolve(dist, "content.css"));
        }
      },
    },
  ],
  resolve: {
    alias: {
      "@shared": resolve(__dirname, "src/shared"),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: resolve(__dirname, "src/sidepanel/index.html"),
        background: resolve(__dirname, "src/background/index.ts"),
        content: resolve(__dirname, "src/content/index.ts"),
      },
      output: {
        entryFileNames: (chunkInfo) => {
          if (chunkInfo.name === "background") return "background.js";
          if (chunkInfo.name === "content") return "content.js";
          return "assets/[name]-[hash].js";
        },
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
