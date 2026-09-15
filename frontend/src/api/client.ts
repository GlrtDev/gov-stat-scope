// frontend/src/api/client.ts
import type { AskRequest, AskResponse, ProblemDetails } from '../types/api';

const getApiBaseUrl = (): string => {
  const configuredBase: string | undefined = import.meta.env.VITE_API_BASE_URL;
  const base = (configuredBase ?? 'http://localhost:8000').trim().replace(/\/+$/, '');

  return base.endsWith('/api/v1') ? base : `${base}/api/v1`;
};

const API_BASE_URL = getApiBaseUrl();

export class ApiError extends Error {
  public status: number;
  public problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(problem.detail || problem.title || 'An unknown error occurred');
    this.name = 'ApiError';
    this.status = problem.status;
    this.problem = problem;
  }
}

export async function askOrchestrator(query: string, sessionId?: string): Promise<AskResponse> {
  const payload: AskRequest = {
    message: query,
    session_id: sessionId,
  };

  try {
    const response = await fetch(`${API_BASE_URL}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, application/problem+json',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      let problem: ProblemDetails;
      const contentType = response.headers.get('content-type');

      if (contentType && contentType.includes('application/problem+json')) {
        problem = await response.json();
      } else {
        problem = {
          type: 'urn:govdata:error:unknown',
          title: response.statusText || 'Unknown Error',
          status: response.status,
          detail: `Received unexpected HTTP status ${response.status}`,
        };
      }
      throw new ApiError(problem);
    }

    const data: AskResponse = await response.json();
    return data;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError({
      type: 'urn:govdata:error:network',
      title: 'Network Error',
      status: 0,
      detail: error instanceof Error ? error.message : 'Failed to connect to the orchestrator API.',
    });
  }
}

export interface ProgressEvent {
  type: string;
  [key: string]: unknown;
}

export async function streamAskOrchestrator(
  query: string,
  sessionId: string | undefined,
  callbacks: {
    onProgress?: (event: ProgressEvent) => void;
    onStarted?: () => void;
    onDone?: (finalAnswer: AskResponse) => void;
    onError?: (error: Error) => void;
  }
): Promise<AskResponse> {
  const payload: AskRequest = {
    message: query,
    session_id: sessionId,
  };

  const response = await fetch(`${API_BASE_URL}/ask/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    // handle HTTP error similar to askOrchestrator
    let problem: ProblemDetails;
    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('application/problem+json')) {
      problem = await response.json();
    } else {
      problem = { type: 'urn:govdata:error:unknown', title: response.statusText, status: response.status, detail: `HTTP ${response.status}` };
    }
    throw new ApiError(problem);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  const parseEvent = (raw: string) => {
    const lines = raw.split('\n');
    let eventType: string | undefined;
    const dataLines: string[] = [];

    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) return;

    const dataStr = dataLines.join('\n');
    try {
      const data = JSON.parse(dataStr);
      if (eventType) {
        data.eventType = eventType;
      }
      return data;
    } catch {
      // ignore malformed
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';

    for (const part of parts) {
      const parsed = parseEvent(part);
      if (!parsed) continue;

      if (parsed.eventType === 'started') {
        callbacks.onStarted?.();
      } else if (parsed.eventType === 'progress') {
        callbacks.onProgress?.(parsed);
      } else if (parsed.eventType === 'done') {
        // final result
        const result = parsed.final as AskResponse;
        callbacks.onDone?.(result);
        return result;
      } else if (parsed.eventType === 'error') {
        const error = new Error(parsed.error || 'Unknown SSE error');
        callbacks.onError?.(error);
        throw error;
      }
    }
  }

  // If loop exits without done, throw error
  throw new ApiError({
    type: 'urn:govdata:error:stream',
    title: 'Stream ended without completion',
    status: 0,
    detail: 'The SSE stream ended before receiving a done event.',
  });
}