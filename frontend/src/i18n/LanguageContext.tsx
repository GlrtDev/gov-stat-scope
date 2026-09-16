import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { translations, type Language, type TranslationKey } from './translations';

interface LanguageContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, params?: Record<string, string | number>) => string;
}

const LanguageContext = createContext<LanguageContextValue | undefined>(undefined);

const STORAGE_KEY = 'govstatscope_language';

function detectBrowserLanguage(): Language {
  if (typeof window === 'undefined') return 'en';

  // Check localStorage first (user override)
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'pl' || stored === 'en') return stored;
  } catch {
    // ignore
  }

  // Fall back to browser/system language
  const browserLang = (navigator.language || navigator.languages?.[0] || 'en').toLowerCase();
  if (browserLang.startsWith('pl')) return 'pl';
  return 'en';
}

export const LanguageProvider = ({ children }: { children: ReactNode }) => {
  const [language, setLanguageState] = useState<Language>(detectBrowserLanguage);

  useEffect(() => {
    document.documentElement.lang = language;
    try {
      window.localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // localStorage unavailable — language still works for this session.
    }
  }, [language]);

  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
  }, []);

  const value = useMemo<LanguageContextValue>(() => {
    const t = (
      key: TranslationKey,
      params?: Record<string, string | number>,
    ): string => {
      let text: string = translations[language][key] ?? key;

      if (params) {
        for (const [param, val] of Object.entries(params)) {
          text = text.split(`{${param}}`).join(String(val));
        }
      }

      return text;
    };

    return { language, setLanguage, t };
  }, [language, setLanguage]);

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
};

export const useLanguage = (): LanguageContextValue => {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error('useLanguage must be used within a LanguageProvider');
  }
  return context;
};