import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { ChatMessage as ChatMessageType } from './types/api';
import { ChatMessage } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';
import { ProgressSteps } from './components/ProgressSteps';
import type { StepDefinition } from './components/ProgressSteps';
import { streamAskOrchestrator, ApiError } from './api/client';
import type { ProgressEvent } from './api/client';
import { LanguageProvider, useLanguage } from './i18n/LanguageContext';
import type { TranslationKey } from './i18n/translations';
import { LanguageSwitcher } from './components/LanguageSwitcher';

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

function getWorkflowSteps(t: Translate): StepDefinition[] {
  return [
    { id: 'router', label: t('step.router') },
    { id: 'api', label: t('step.api') },
    { id: 'analyst', label: t('step.analyst') },
    { id: 'complete', label: t('step.complete') },
  ];
}

function formatEventStatus(event: ProgressEvent, t: Translate): string {
  const toText = (value: unknown, fallback: string): string =>
    typeof value === 'string' && value.length > 0 ? value : fallback;

  switch (event.type) {
    case 'route_selected':
      return t('progress.route_selected', { source: toText(event.source, '?') });
    case 'gus_search_started':
      return t('progress.gus_search_started');
    case 'subject_selected':
      return t('progress.subject_selected', {
        level: toText(event.level, '?'),
        name: toText(event.name, '?'),
      });
    case 'variable_selected':
      return t('progress.variable_selected', { name: toText(event.name, '?') });
    case 'data_fetched': {
      const [start, end] = Array.isArray(event.years) ? event.years.map(String) : [];
      return t('progress.data_fetched', { start: start ?? '?', end: end ?? '?' });
    }
    case 'fred_started':
      return t('progress.fred_started');
    case 'fred_completed':
      return t('progress.fred_completed', { series_id: toText(event.series_id, '?') });
    case 'analyst_started':
      return t('progress.analyst_started');
    case 'analyst_completed':
      return t('progress.analyst_completed');
    case 'error':
      return t('progress.error', {
        message: toText(event.message, t('progress.unknown_error')),
      });
    default:
      return t('progress.working');
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

const AppContent: React.FC = () => {
  const { t } = useLanguage();

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

  const workflowSteps = useMemo<StepDefinition[]>(() => getWorkflowSteps(t), [t]);

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
    setProgressMessages([t('progress.starting')]);
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

            setProgressMessages((prev) => [...prev, formatEventStatus(event, t)]);

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
        content: finalAnswer || responseAnswer || t('chat.no_response'),
        source: response.source,
        metadata: response.metadata,
      };
      setMessages((prev) => [...prev, assistantMessage]);
      setProgressSteps({ activeStepId: 'complete', errorStepId: undefined });
    } catch (error) {
      let errorMessage = t('errors.unexpected');
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
    <div className="app-shell d-flex flex-column">
      <header className="app-header py-3 px-3 px-md-4">
        <div className="container-xl d-flex align-items-center justify-content-between gap-2 gap-md-3">
          <div className="d-flex align-items-center gap-2 gap-md-3 min-w-0">
            <div className="brand-mark" aria-hidden="true">GS</div>
            <div className="min-w-0">
              <h1 className="app-title mb-0 text-truncate">{t('app.title')}</h1>
              <p className="app-tagline mb-0 d-none d-sm-block">{t('app.tagline')}</p>
            </div>
          </div>
          <div className="d-flex align-items-center gap-2 flex-shrink-0">
            <LanguageSwitcher />
            <span className="session-chip d-none d-md-inline-flex">
              {t('app.session')} {sessionId.slice(0, 8)}
            </span>
          </div>
        </div>
      </header>

      <main className="app-main flex-grow-1 d-flex flex-column container-xl px-3 px-md-4 py-4">
        {isLoading && (
          <div className="mb-3">
            <ProgressSteps
              steps={workflowSteps}
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
                  {isProgressExpanded ? t('session.show_less') : t('session.show_all')}
                  <span aria-hidden="true">{isProgressExpanded ? ' ▲' : ' ▼'}</span>
                </button>
              )}
            </div>
          </div>
        )}

        {messages.length === 0 ? (
          <div className="empty-state my-auto text-center p-4 p-md-5">
            <h2 className="fw-semibold mb-3">{t('empty.title')}</h2>
            <p className="text-secondary mb-4">{t('empty.subtitle')}</p>
            <div className="d-flex flex-column flex-sm-row gap-2 align-items-stretch align-items-sm-center justify-content-center">
              <span className="example-chip example-chip--gus">{t('empty.example_gus')}</span>
              <span className="example-chip example-chip--fred">{t('empty.example_fred')}</span>
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

      <footer className="app-footer py-3 px-3 px-md-4">
        <div className="container-xl">
          <ChatInput
            onSend={handleSendMessage}
            isLoading={isLoading}
            placeholder={t('chat.input_placeholder')}
          />
        </div>
      </footer>
    </div>
  );
};

export const App: React.FC = () => (
  <LanguageProvider>
    <AppContent />
  </LanguageProvider>
);

export default App;