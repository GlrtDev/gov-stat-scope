// frontend/src/components/ChatMessage.tsx
import React from 'react';
import type { ChatMessage as ChatMessageType } from '../types/api';
import { SourceBadge } from './SourceBadge';

type ChatMessageProps = {
  message: ChatMessageType;
};

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const isError = Boolean(message.isError);

  return (
    <div className={`chat-row ${isUser ? 'justify-content-end' : 'justify-content-start'}`}>
      <div
        className={`message-bubble ${isUser ? 'message-user' : 'message-assistant'} ${isError ? 'message-error' : ''} d-flex flex-column gap-2`}
      >
        <div className="d-flex align-items-center justify-content-between gap-2">
          <span
            className={`message-author ${isUser ? 'message-author--user' : isError ? 'message-author--error' : 'message-author--assistant'}`}
          >
            {isUser ? 'You' : 'GovStatScope AI'}
          </span>
          {!isUser && <SourceBadge source={message.source} isError={isError} />}
        </div>
        <p className="mb-0 message-content">{message.content}</p>
      </div>
    </div>
  );
};