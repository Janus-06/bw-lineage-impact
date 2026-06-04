export type OutputFormat = 'json' | 'md' | 'mermaid';

export interface RenderedResponse {
  format: string;
  content: string;
}

export interface HealthResponse {
  status: string;
  local_only: boolean;
  read_only: boolean;
  llm_enabled_by_default: boolean;
  version: string;
}

export interface RuntimeConfigResponse {
  storage: 'process-memory';
  bw: {
    configured: boolean;
    url: string | null;
    user: string | null;
    password: string | null;
    client: string | null;
    language: string;
    verify_ssl: boolean;
  };
  llm: {
    enabled: boolean;
    configured: boolean;
    base_url: string | null;
    model: string | null;
    api_key: string | null;
  };
}

export interface RuntimeConfigRequest {
  bw?: {
    url: string;
    user: string;
    password: string;
    client: string;
    language: string;
    verify_ssl: boolean;
  };
  llm?: {
    enabled: boolean;
    base_url?: string;
    model?: string;
    api_key?: string;
  };
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health');
  return parseJsonResponse<HealthResponse>(response);
}

export async function getRuntimeConfig(): Promise<RuntimeConfigResponse> {
  const response = await fetch('/api/runtime-config');
  return parseJsonResponse<RuntimeConfigResponse>(response);
}

export async function putRuntimeConfig(body: RuntimeConfigRequest): Promise<RuntimeConfigResponse> {
  const response = await fetch('/api/runtime-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<RuntimeConfigResponse>(response);
}

export async function clearRuntimeConfig(): Promise<RuntimeConfigResponse> {
  const response = await fetch('/api/runtime-config', { method: 'DELETE' });
  return parseJsonResponse<RuntimeConfigResponse>(response);
}

export async function postRendered(path: string, body: unknown): Promise<RenderedResponse> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<RenderedResponse>(response);
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as unknown;
  if (!response.ok) {
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : response.statusText;
    throw new Error(detail);
  }
  return payload as T;
}
