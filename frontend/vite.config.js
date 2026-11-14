import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// EGM-53: la CSP estricta de index.html bloquea el preámbulo inline de React Refresh en
// `vite dev`; este plugin la retira SOLO en el servidor de desarrollo. El build la conserva.
const devCspPlugin = {
  name: 'mcp2p-dev-csp',
  apply: 'serve',
  transformIndexHtml(html) {
    return html.replace(/<meta http-equiv="Content-Security-Policy"[^>]*>\s*/, '');
  }
};

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), devCspPlugin],
  base: './',
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false
  },
  server: {
    port: 5173,
    strictPort: true
  }
});
