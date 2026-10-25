import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { realpathSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
const root = fileURLToPath(new URL(".", import.meta.url));
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // Permit locally installed assets even when node_modules is a workspace symlink.
    fs: {
      allow: [
        root,
        realpathSync(fileURLToPath(new URL("./node_modules", import.meta.url))),
      ],
    },
  },
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
});
