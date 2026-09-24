import React, { useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { useLanguage } from '../i18n/LanguageContext';

type ChatInputProps = {
  onSend: (query: string) => void | Promise<void>;
  isLoading: boolean;
  placeholder?: string;
};


export const ChatInput: React.FC<ChatInputProps> = ({ onSend, isLoading, placeholder }) => {
  const { t } = useLanguage();
  const [value, setValue] = useState<string>('');

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = value.trim();

    if (!query || isLoading) {
      return;
    }

    await onSend(query);
    setValue('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit(event as unknown as FormEvent<HTMLFormElement>);
    }
  };

  return (
    <form className="chat-composer d-flex gap-2 align-items-end" onSubmit={handleSubmit}>
      <div className="flex-grow-1">
        <label htmlFor="govstat-query-input" className="visually-hidden">
          {t('chat.input_label')}
        </label>
        <textarea
          id="govstat-query-input"
          className="composer-textarea"
          rows={2}
          placeholder={placeholder}
          value={value}
          disabled={isLoading}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>

      <button
        type="submit"
        className="btn-brand"
        disabled={isLoading || !value.trim()}
      >
        {isLoading ? (
          <>
            <span className="spinner-border spinner-border-sm" aria-hidden="true" />
            <span className="d-none d-sm-inline">{t('chat.working')}</span>
          </>
        ) : (
          <span className="d-none d-sm-inline">{t('chat.send')}</span>
        )}
        {isLoading ? null : (
          <span className="d-sm-none" aria-hidden="true">➤</span>
        )}
      </button>
    </form>
  );
};