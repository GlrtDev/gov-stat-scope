// frontend/src/App.tsx
import React, { useEffect, useRef, useState } from 'react';
import type { ChatMessage as ChatMessageType } from './types/api';
import { ChatMessage } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';
import { ProgressSteps } from './components/ProgressSteps';
import type { StepDefinition } from './components/ProgressSteps';
import { streamAskOrchestrator, ApiError } from './api/client';
import type { ProgressEvent } from './api/client';

const WORKFLOW_STEPS: StepDefinition[] = [
  { id: 'router', label: 'Route' },
  { id: 'api', label: 'Fetch' },
  { id: 'analyst', label: 'Analyze' },
  { id: 'complete', label: 'Complete' },
];

function formatEventStatus(event: ProgressEvent): string {
  switch (event.type) {
    case 'route_selected':
      return `Source: ${event.source}`;
    case 'gus_search_started':
      return 'Searching GUS subjects...';
    case 'subject_selected':
      return `${event.level}-level subject: ${event.name}`;
    case 'variable_selected':
      return `Variable: ${event.name}`;
    case 'data_fetched':
      return `Fetched: ${event.years?.[0] || '?'}–${event.years?.[1] || '?'}`;
    case 'fred_started':
      return 'Fetching FRED data...';
    case 'fred_completed':
      return `FRED series: ${event.series_id}`;
    case 'analyst_started':
      return 'Analyzing data...';
    case 'analyst_completed':
      return 'Analysis complete';
    case 'error':
      return `Error: ${event.message || 'Unknown error'}`;
    default:
      return 'Working...';
  }
}

function extractFinalAnswer(event: unknown): string | null {
  if (!event || typeof event !== 'object') return null;
  const candidate = event as Record<string, unknown>;

  // Case 1: { final: { final_answer, answer } }
  const nestedFinal = candidate.final;
  if (nestedFinal && typeof nestedFinal === 'object') {
    const finalObj = nestedFinal as Record<string, unknown>;
    const nestedAnswer = finalObj.final_answer ?? finalObj.answer;
    if (typeof nestedAnswer === 'string' && nestedAnswer) return nestedAnswer;
  }

  // Case 2: SSE wrapper { type: "done", data: { final: { ... } } }
  const data = candidate.data;
  if (data && typeof data === 'object') {
    const dataObj = data as Record<string, unknown>;
    const dataFinal = dataObj.final;
    if (dataFinal && typeof dataFinal === 'object') {
      const finalObj = dataFinal as Record<string, unknown>;
      const dataAnswer = finalObj.final_answer ?? finalObj.answer;
      if (typeof dataAnswer === 'string' && dataAnswer) return dataAnswer;
    }
    const directDataAnswer = dataObj.final_answer ?? dataObj.answer;
    if (typeof directDataAnswer === 'string' && directDataAnswer) return directDataAnswer;
  }

  // Case 3: direct { final_answer, answer }
  const directAnswer = candidate.final_answer ?? candidate.answer;
  if (typeof directAnswer === 'string' && directAnswer) return directAnswer;

  return null;
}

export const App: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [progressSteps, setProgressSteps] = useState({
    activeStepId: undefined as string | undefined,
    errorStepId: undefined as string | undefined,
  });
  const [progressMessages, setProgressMessages] = useState<string[]>([]);
  const [isProgressExpanded, setIsProgressExpanded] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSessionId(crypto.randomUUID());
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const visibleProgressMessages = isProgressExpanded
    ? [...progressMessages].reverse()
    : progressMessages.slice(-3).reverse();

  const handleSendMessage = async (query: string) => {
    const userMessage: ChatMessageType = {
      id: crypto.randomUUID(),
      role: 'user',
      content: query,
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);
    setProgressMessages(['Starting...']);
    setIsProgressExpanded(false);
    setProgressSteps({ activeStepId: 'router', errorStepId: undefined });

    let finalAnswer = '';

    try {
      const response = await streamAskOrchestrator(query, sessionId, {
        onProgress: (event) => {
          // Extract final answer if this SSE event contains it
          const extractedAnswer = extractFinalAnswer(event);
          if (extractedAnswer) {
            finalAnswer = extractedAnswer;
            return;
          }

          // Format normal progress events only
          if (event && typeof event.type === 'string') {
            if (event.type === 'done' || event.type === 'final') return;

            setProgressMessages((prev) => [...prev, formatEventStatus(event)]);

            if (event.type === 'route_selected') {
              setProgressSteps({ activeStepId: 'api', errorStepId: undefined });
            } else if (event.type === 'data_fetched') {
              setProgressSteps({ activeStepId: 'analyst', errorStepId: undefined });
            } else if (event.type === 'analyst_completed') {
              setProgressSteps({ activeStepId: 'complete', errorStepId: undefined });
            } else if (event.type === 'error') {
              setProgressSteps({ activeStepId: undefined, errorStepId: 'complete' });
            }
          }
        },
      });

      // Fallback extraction from the response object itself
      const responseAnswer = extractFinalAnswer(response) ?? response.answer;

      const assistantMessage: ChatMessageType = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: finalAnswer || responseAnswer || 'No response received.',
        source: response.source,
        metadata: response.metadata,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      setProgressSteps({ activeStepId: 'complete', errorStepId: undefined });
    } catch (error) {
      let errorMessage = 'An unexpected error occurred. Please try again.';
      let metadata: Record<string, unknown> | undefined;

      if (error instanceof ApiError) {
        errorMessage = error.problem.detail;
        metadata = {
          status: error.problem.status,
          type: error.problem.type,
          title: error.problem.title,
        };
      } else {
        errorMessage = error instanceof Error ? error.message : errorMessage;
      }

      const errorChatMsg: ChatMessageType = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: errorMessage,
        isError: true,
        metadata,
      };
      setMessages((prev) => [...prev, errorChatMsg]);
      setProgressSteps({ activeStepId: undefined, errorStepId: 'complete' });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="app-shell d-flex flex-column min-vh-100">
      <header className="app-header border-bottom py-3 px-3 px-md-4 sticky-top bg-white shadow-sm">
        <div className="container-xl d-flex align-items-center justify-content-between gap-3">
          <div className="d-flex align-items-center gap-3">
            <div className="brand-mark" aria-hidden="true">GS</div>
            <div>
              <h1 className="app-title mb-0 fw-bold text-body-emphasis">GovStatScope AI</h1>
              <p className="mb-0 small text-secondary">Polish &amp; US government data assistant</p>
            </div>
          </div>
          <span className="session-chip badge rounded-pill border-0 bg-light text-body-secondary fw-normal font-monospace">
            Session: {sessionId.slice(0, 8)}
          </span>
        </div>
      </header>

      <main className="flex-grow-1 d-flex flex-column container-xl px-3 px-md-4 py-4 overflow-auto">
        {isLoading && (
          <div className="mb-3">
            <ProgressSteps
              steps={WORKFLOW_STEPS}
              currentStepId={progressSteps.activeStepId}
              errorStepId={progressSteps.errorStepId}
            />
            <div className="small mt-2 fw-semibold">
              {visibleProgressMessages.map((msg, index) => (
                <div
                  key={`${index}-${msg}`}
                  className={`progress-log-line ${index === 0 ? 'text-body' : 'text-secondary'}`}
                >
                  {msg}
                </div>
              ))}
              {progressMessages.length > 3 && (
                <button
                  type="button"
                  onClick={() => setIsProgressExpanded(!isProgressExpanded)}
                  className="btn btn-link btn-sm p-0 text-decoration-none fw-semibold d-inline-flex align-items-center gap-1"
                  aria-expanded={isProgressExpanded}
                >
                  {isProgressExpanded ? 'Show less' : 'Show all'}
                  <span aria-hidden="true">{isProgressExpanded ? ' ▲' : ' ▼'}</span>
                </button>
              )}
            </div>
          </div>
        )}

        {messages.length === 0 ? (
          <div className="empty-state my-auto text-center rounded-4 border border-dashed p-5 bg-white shadow-sm">
            <h2 className="fw-bold mb-3">Ask about government data</h2>
            <p className="text-secondary mb-4">
              Try a query routed to GUS or FRED. The assistant will normalize the response and explain the source.
            </p>
            <div className="d-flex flex-column gap-2 align-items-center">
              <span className="badge bg-primary-subtle text-dark border border-primary-subtle rounded-pill px-3 py-2 fw-normal">
                Example: Jaka była cena pszenicy w 2017?
              </span>
              <span className="badge bg-success-subtle text-dark border border-success-subtle rounded-pill px-3 py-2 fw-normal">
                Example: Jak zmieniał się PKB Polski w ostatnich latach 2005-2020?
              </span>
            </div>
          </div>
        ) : (
          <div className="chat-stream d-flex flex-column gap-3 pb-2">
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </main>

      <footer className="app-footer border-top py-3 px-3 px-md-4 bg-white">
        <div className="container-xl">
          <ChatInput onSend={handleSendMessage} isLoading={isLoading} />
        </div>
      </footer>
    </div>
  );
};

export default App;