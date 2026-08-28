import React, { useState, useEffect, useRef } from 'react';
import type { ChatMessage as ChatMessageType } from './types/api';
import { ChatMessage } from './components/ChatMessage';
import { ChatInput } from './components/ChatInput';
import { askOrchestrator, ApiError } from './api/client';

export const App: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [sessionId, setSessionId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

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
      let metadata: Record<string, any> | undefined = undefined;

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
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 py-4 px-6 sticky top-0 z-10 shadow-sm">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900 tracking-tight">GovStatScope AI</h1>
          <span className="text-xs font-mono bg-gray-100 text-gray-600 px-2 py-1 rounded border border-gray-200">
            Session: {sessionId.split('-')[0]}
          </span>
        </div>
      </header>

      <main className="flex-1 w-full max-w-4xl mx-auto p-4 md:p-6 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500">
            <p className="text-center">Start a conversation by asking about government data.<br/>Example: "What is the inflation rate in Poland?"</p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </main>

      <div className="w-full max-w-4xl mx-auto">
        <ChatInput onSend={handleSendMessage} isLoading={isLoading} />
      </div>
    </div>
  );
};

export default App;