import { AskRequest, AskResponse, ProblemDetails } from '../types/api';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

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
        'Accept': 'application/json, application/problem+json',
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