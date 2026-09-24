// frontend/src/components/LanguageSwitcher.tsx
import React from 'react';
import { useLanguage } from '../i18n/LanguageContext';
import type { Language } from '../i18n/translations';

const OPTIONS: { code: Language; label: string }[] = [
  { code: 'en', label: 'EN' },
  { code: 'pl', label: 'PL' },
];

export const LanguageSwitcher: React.FC = () => {
  const { language, setLanguage } = useLanguage();

  return (
    <div className="language-switcher" role="group" aria-label="Language switcher">
      {OPTIONS.map((option) => {
        const isActive = language === option.code;

        return (
          <button
            key={option.code}
            type="button"
            onClick={() => setLanguage(option.code)}
            className={`language-option ${isActive ? 'language-option--active' : ''}`}
            aria-pressed={isActive}
            title={option.label}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
};