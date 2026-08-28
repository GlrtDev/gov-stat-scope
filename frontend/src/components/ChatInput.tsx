import React, { useState, FormEvent } from 'react';

interface ChatInputProps {
  onSend: (message: string) => void;
  isLoading: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, isLoading }) => {
  const [input, setInput] = useState('');

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (trimmed && !isLoading) {
      onSend(trimmed);
      setInput('');
    }
  };

  return (
    <form 
      onSubmit={handleSubmit} 
      className="flex items-center gap-3 p-4 bg-white border-t border-gray-200 sticky bottom-0"
    >
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        disabled={isLoading}
        placeholder="Ask about government statistical data..."
        className="flex-1 px-4 py-3 bg-gray-50 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 focus:bg-white transition-colors disabled:opacity-60 disabled:cursor-not-allowed text-gray-900 placeholder-gray-500"
        autoComplete="off"
      />
      <button
        type="submit"
        disabled={isLoading || !input.trim()}
        className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed min-w-[100px]"
      >
        {isLoading ? (
          <span className="flex items-center justify-center gap-2">
            <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Wait
          </span>
        ) : (
          'Send'
        )}
      </button>
    </form>
  );
};