// frontend/src/components/SourceBadge.tsx
import React from 'react';

interface SourceBadgeProps {
  source?: string;
  isError?: boolean;
}

export const SourceBadge: React.FC<SourceBadgeProps> = ({ source, isError = false }) => {
  if (isError) {
    return <span className="source-badge source-badge--error">Error</span>;
  }

  const normalizedSource = (source ?? '').trim().toUpperCase();

  if (!normalizedSource || normalizedSource === 'UNSUPPORTED') {
    return null;
  }

  const label =
    normalizedSource === 'GUS'
      ? 'GUS · PL'
      : normalizedSource === 'FRED'
        ? 'FRED · US'
        : normalizedSource;

  const variant =
    normalizedSource === 'GUS'
      ? 'source-badge--gus'
      : normalizedSource === 'FRED'
        ? 'source-badge--fred'
        : 'source-badge--other';

  return <span className={`source-badge ${variant}`}>{label}</span>;
};