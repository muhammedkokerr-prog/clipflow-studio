import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg'],
      manifest: {
        name: 'ClipFlow Studio',
        short_name: 'ClipFlow',
        description: 'Dikey videolar için mobil kurgu stüdyosu',
        lang: 'tr',
        theme_color: '#171b19',
        background_color: '#f1f3ee',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any maskable' }],
      },
    }),
  ],
  server: { proxy: { '/api': 'http://localhost:8000', '/health': 'http://localhost:8000' } },
});