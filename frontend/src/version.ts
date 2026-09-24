// frontend/src/version.ts — build metadata injected by scripts/deploy_frontend.ps1

/** Deploy version injected at build time (e.g. "sha-1a2b3c4"); "dev" for local runs. */
export const APP_VERSION: string =
  import.meta.env.VITE_APP_VERSION?.trim() || 'dev';

/** UTC build timestamp injected at build time; undefined for local dev. */
export const BUILD_TIME_UTC: string | undefined =
  import.meta.env.VITE_BUILD_TIME?.trim() || undefined;