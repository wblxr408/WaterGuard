import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 三个只读数据源都走开发服务器代理，浏览器只访问 5173，同源无需 CORS。
// /fx → day1 Fixture（8000）；/bm → 同学 1 的 22 个真实工况（8001）；/ab → 真实 A/B 对照（8002）。
const api = (port: number, prefix: string) => ({
  target: `http://127.0.0.1:${port}`,
  rewrite: (path: string) => path.replace(new RegExp(`^${prefix}`), ''),
})

export default defineConfig({
  plugins: [react()],
  server: {
    // 固定 IPv4：Node 默认把 localhost 绑到 ::1，部分 Windows 浏览器/代理会连不上；接口服务也都在 127.0.0.1。
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/fx': api(8000, '/fx'),
      '/bm': api(8001, '/bm'),
      '/ab': api(8002, '/ab'),
    },
  },
})
