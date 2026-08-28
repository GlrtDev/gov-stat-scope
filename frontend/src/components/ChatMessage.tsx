import React from 'react';
import type { ChatMessage as ChatMessageType } from '../types/api';
import { SourceBadge } from './SourceBadge';

interface ChatMessageProps {
  message: ChatMessageType;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.role === 'user';

  const containerAlignment = isUser ? 'justify-end' : 'justify-start';
  const bubbleStyles = isUser 
    ? 'bg-blue-600 text-white' 
    : message.isError 
      ? 'bg-red-50 text-red-900 border border-red-200' 
      : 'bg-white text-gray-900 border border-gray-200 shadow-sm';

  return (
    <div className={`flex w-full ${containerAlignment} mb-6`}>
      <div className={`max-w-[85%] md:max-w-[75%] rounded-2xl p-5 ${bubbleStyles} flex flex-col gap-3`}>
        
        {!isUser && message.source && (
          <div>
            <SourceBadge source={message.source} />
          </div>
        )}

        <div className="whitespace-pre-wrap leading-relaxed">
          {message.content}
        </div>

        {!isUser && message.metadata && Object.keys(message.metadata).length > 0 && (
          <div className={`mt-2 pt-3 border-t ${message.isError ? 'border-red-200' : 'border-gray-200'}`}>
            <h4 className="text-xs font-bold uppercase tracking-wider mb-2 opacity-70">Metadata</h4>
            <div className="grid grid-cols-1 gap-1 text-sm opacity-90">
              {Object.entries(message.metadata).map(([key, value]) => (
                <div key={key} className="flex flex-col sm:flex-row sm:gap-2">
                  <span className="font-semibold text-xs mt-0.5 capitalize min-w-max">
                    {key.replace(/_/g, ' ')}:
                  </span>
                  <span className="font-mono text-xs break-all">
                    {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        
      </div>
    </div>
  );
};