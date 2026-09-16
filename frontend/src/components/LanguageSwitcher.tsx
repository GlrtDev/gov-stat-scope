import React from 'react';
import { useLanguage } from '../i18n/LanguageContext';
import type { Language } from '../i18n/translations';

const OPTIONS: { code: Language; label: string; flag: string }[] = [
  { code: 'en', label: 'EN', flag: '🇺🇸' },
  { code: 'pl', label: 'PL', flag: '🇵🇱' },
];

export const LanguageSwitcher: React.FC = () => {
  const { language, setLanguage } = useLanguage();

  return (
    <div
      className="language-switcher d-flex align-items-center gap-1"
      role="group"
      aria-label="Language switcher"
    >
      {OPTIONS.map((option) => {
        const isActive = language === option.code;

        return (
          <button
            key={option.code}
            type="button"
            onClick={() => setLanguage(option.code)}
            className={`btn btn-sm border rounded-pill px-2 py-1 d-inline-flex align-items-center gap-1 ${
              isActive ? 'btn-primary' : 'btn-light'
            }`}
            aria-pressed={isActive}
            title={option.label}
          >
            <span aria-hidden="true">{option.flag}</span>
            <span className="d-none d-sm-inline">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
};