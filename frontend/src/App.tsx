// frontend/src/App.tsx
import React, { useEffect, useRef, useState } from 'react';
import type { ChatMessage as ChatMessageType } from './types/api';
import { ChatMessage } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';
import { askOrchestrator, ApiError } from './api/client';

export const App: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [sessionId, setSessionId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSessionId(crypto.randomUUID());
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async (query: string) => {
    const userMessage: ChatMessageType = {
      id: crypto.randomUUID(),
      role: 'user',
      content: query,
    };

    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const response = await askOrchestrator(query, sessionId);
      const assistantMessage: ChatMessageType = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: response.answer,
        source: response.source,
        metadata: response.metadata,
      };
      setMessages((prev) => [...prev, assistantMessage]);
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
      }

      const errorChatMsg: ChatMessageType = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: errorMessage,
        isError: true,
        metadata,
      };
      setMessages((prev) => [...prev, errorChatMsg]);
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
        {messages.length === 0 ? (
          <div className="empty-state my-auto text-center rounded-4 border border-dashed p-5 bg-white shadow-sm">
            <h2 className="fw-bold mb-3">Ask about government data</h2>
            <p className="text-secondary mb-4">
              Try a query routed to GUS or FRED. The assistant will normalize the response and explain the source.
            </p>
            <div className="d-flex flex-column gap-2 align-items-center">
              <span className="badge text-bg-primary-subtle border border-primary-subtle rounded-pill px-3 py-2 fw-normal">
                Example: What is the population of Poland over the last few years?
              </span>
              <span className="badge text-bg-success-subtle border border-success-subtle rounded-pill px-3 py-2 fw-normal">
                Example: How has US GDP changed recently?
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
