import type { Backend } from './backend';
import { HttpBackend } from './httpBackend';
import { MockBackend } from './mockBackend';

const kind = (import.meta.env.VITE_BACKEND as string | undefined) ?? 'mock';

export const backend: Backend = kind === 'http'
  ? new HttpBackend((import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8765')
  : new MockBackend();

export * from './backend';
