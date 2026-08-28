export interface AskRequest {
  message: string;
  session_id?: string;
}

export interface AskResponse {
  answer: string;
  source: string;
  metadata: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  source?: string;
  metadata?: Record<string, any>;
  isError?: boolean;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  errors?: any[];
}