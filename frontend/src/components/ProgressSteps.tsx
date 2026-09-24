// frontend/src/components/ProgressSteps.tsx
import React from 'react';

export type StepStatus = 'pending' | 'active' | 'completed' | 'error';

export interface StepDefinition {
  id: string;
  label: string;
}

type ProgressStepsProps = {
  steps: StepDefinition[];
  currentStepId?: string;
  errorStepId?: string;
};

export const ProgressSteps: React.FC<ProgressStepsProps> = ({ steps, currentStepId, errorStepId }) => {
  const activeIndex = steps.findIndex((step) => step.id === currentStepId);
  const errorIndex = steps.findIndex((step) => step.id === errorStepId);
  // Steps before the active (or error) step are considered completed
  const completedBoundary = activeIndex !== -1 ? activeIndex : errorIndex;

  return (
    <div className="progress-steps" role="status" aria-live="polite">
      {steps.map((step, index) => {
        const isActive = currentStepId === step.id;
        const isError = errorStepId === step.id;
        const status: StepStatus = isError
          ? 'error'
          : isActive
            ? 'active'
            : completedBoundary !== -1 && index < completedBoundary
              ? 'completed'
              : 'pending';

        return (
          <div
            key={step.id}
            className={`progress-step progress-step--${status}`}
            aria-current={isActive ? 'step' : undefined}
          >
            <span className="progress-step-index" aria-hidden="true">
              {status === 'completed' ? '✓' : status === 'error' ? '!' : index + 1}
            </span>
            <span className="progress-step-label">{step.label}</span>
          </div>
        );
      })}
    </div>
  );
};