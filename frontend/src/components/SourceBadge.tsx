import React from 'react';

interface SourceBadgeProps {
  source: string;
}

export const SourceBadge: React.FC<SourceBadgeProps> = ({ source }) => {
  const normalizedSource = source.toUpperCase();
  
  let colorClasses = 'bg-gray-100 text-gray-800 border-gray-200';
  
  if (normalizedSource === 'GUS') {
    colorClasses = 'bg-blue-100 text-blue-800 border-blue-200';
  } else if (normalizedSource === 'FRED') {
    colorClasses = 'bg-green-100 text-green-800 border-green-200';
  }

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${colorClasses}`}>
      {normalizedSource}
    </span>
  );
};