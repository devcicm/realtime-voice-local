import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  publicDir: 'public',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    target: 'es2022',
    rollupOptions: {
      input: {
        main: 'index.html',
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    strictPort: false,
    open: false,
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['tests/**/*.test.js'],
  },
});
