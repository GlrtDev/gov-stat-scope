// frontend/src/components/ProgressSteps.tsx
import React from 'react';

export type StepStatus = 'pending' | 'active' | 'completed' | 'error';

export interface StepDefinition {
  id: string;
  label: string;
}

type ProgressStepsProps = {
  steps: StepDefinition[];
  currentStepId?: string; // id of the active step
  errorStepId?: string;
};

export const ProgressSteps: React.FC<ProgressStepsProps> = ({ steps, currentStepId, errorStepId }) => {
  return (
    <div className="d-flex flex-wrap gap-2 align-items-center">
      {steps.map((step) => {
        const isActive = currentStepId === step.id;
        const isError = errorStepId === step.id;
        const status: StepStatus = isError
          ? 'error'
          : isActive
            ? 'active'
            : 'completed'; // we can assume all steps before active are completed

        const badgeColor = status === 'error'
          ? 'bg-danger'
          : status === 'active'
            ? 'bg-warning text-dark'
            : 'bg-success text-white';

        const pulse = status === 'active' ? ' progress-step-pulse' : '';

        return (
          <div
            key={step.id}
            className={`px-3 py-2 rounded-3 border fw-semibold small ${badgeColor} ${pulse}`}
            style={{ minWidth: '120px', textAlign: 'center' }}
          >
            {step.label}
          </div>
        );
      })}
    </div>
  );
};