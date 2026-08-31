// frontend/src/components/ChatMessage.tsx
import React from 'react';
import type { ChatMessage as ChatMessageType } from '../types/api';

type SourceBadgeProps = {
  source?: string;
  isError?: boolean;
};

const SourceBadge: React.FC<SourceBadgeProps> = ({ source, isError }) => {
  if (isError) {
    return <span className="badge text-bg-danger rounded-pill fw-semibold">Error</span>;
  }

  const normalizedSource = (source ?? '').trim().toUpperCase();

  if (!normalizedSource || normalizedSource === 'UNSUPPORTED') {
    return null;
  }

  const label = normalizedSource === 'GUS' ? 'GUS · Poland' : normalizedSource === 'FRED' ? 'FRED · US' : normalizedSource;
  const className =
    normalizedSource === 'GUS'
      ? 'badge text-bg-primary rounded-pill fw-semibold'
      : normalizedSource === 'FRED'
        ? 'badge text-bg-success rounded-pill fw-semibold'
        : 'badge text-bg-secondary rounded-pill fw-semibold';

  return <span className={className}>{label}</span>;
};

type ChatMessageProps = {
  message: ChatMessageType;
};

export const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const isError = Boolean(message.isError);

  return (
    <div className={`chat-row ${isUser ? 'justify-content-end' : 'justify-content-start'}`}>
      <div className={`message-bubble ${isUser ? 'message-user' : 'message-assistant'} ${isError ? 'message-error' : ''} d-flex flex-column gap-2 p-3 rounded-4 shadow-sm`}>
        <div className="d-flex align-items-center justify-content-between gap-2">
          <span className={`message-author fw-semibold small ${isUser ? 'text-white' : isError ? 'text-danger-emphasis' : 'text-body-secondary'}`}>
            {isUser ? 'You' : 'GovStatScope AI'}
          </span>
          {!isUser && <SourceBadge source={message.source} isError={isError} />}
        </div>

        <p className={`mb-0 message-content ${isUser ? 'text-white' : ''}`}>{message.content}</p>
      </div>
    </div>
  );
};
