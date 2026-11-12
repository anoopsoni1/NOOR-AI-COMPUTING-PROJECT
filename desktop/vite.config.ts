import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "frontend",
  base: "./", // relative asset paths so the Electron file:// build works
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { outDir: "../dist/frontend", emptyOutDir: true },
});
