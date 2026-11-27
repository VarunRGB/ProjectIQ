import type { Backend } from './backend';
import { HttpBackend } from './httpBackend';
import { MockBackend } from './mockBackend';

const kind = (import.meta.env.VITE_BACKEND as string | undefined) ?? 'mock';

function operatorToken(): string {
  const key = 'projectiq.localOperatorToken';
  const saved = sessionStorage.getItem(key);
  if (saved) return saved;
  const token = window.prompt('ProjectIQ local operator token (from your bridge terminal)')?.trim() ?? '';
  if (token) sessionStorage.setItem(key, token);
  return token;
}

export const backend: Backend = kind === 'http'
  ? new HttpBackend((import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8765', operatorToken())
  : new MockBackend();

export * from './backend';
