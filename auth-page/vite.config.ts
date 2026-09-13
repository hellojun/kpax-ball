import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Served at https://kpax.bout.network/auth/
  base: "/auth/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Inline small assets to keep the fetch count low
    assetsInlineLimit: 4096,
  },
  server: {
    port: 3100,
    strictPort: true,
  },
  preview: {
    port: 3100,
  },
});
