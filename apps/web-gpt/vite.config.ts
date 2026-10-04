import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = Object.fromEntries(
  [
    ["/ab", 8002],
    ["/bm", 8001],
    ["/fx", 8000],
  ].map(([prefix, port]) => [
    prefix,
    {
      target: `http://127.0.0.1:${port}`,
      changeOrigin: true,
      rewrite: (path: string) => path.replace(new RegExp(`^${prefix}`), ""),
    },
  ]),
);

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
});
