import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// VITE_BACKEND=mock (default) uses generated fixtures; VITE_BACKEND=http talks to
// the local ProjectIQ bridge at VITE_API_BASE (see src/api/httpBackend.ts).
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  test: { environment: 'node' },
} as any);
