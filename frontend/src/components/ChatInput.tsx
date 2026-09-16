import React, { FormEvent, KeyboardEvent, useState } from 'react';
import { useLanguage } from '../i18n/LanguageContext';

type ChatInputProps = {
  onSend: (query: string) => void | Promise<void>;
  isLoading: boolean;
};

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, isLoading }) => {
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
          className="form-control shadow-sm rounded-4 px-3 py-3"
          rows={2}
          placeholder={t('chat.input_placeholder')}
          value={value}
          disabled={isLoading}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>

      <button
        type="submit"
        className="btn btn-primary btn-lg px-4 rounded-4 shadow-sm align-self-end"
        disabled={isLoading || !value.trim()}
      >
        {isLoading ? (
          <>
            <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
            {t('chat.working')}
          </>
        ) : (
          t('chat.send')
        )}
      </button>
    </form>
  );
};