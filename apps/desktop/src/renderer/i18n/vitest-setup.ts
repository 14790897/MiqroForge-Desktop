/**
 * Vitest setup for the MiQroForge renderer i18n layer.
 *
 * Component unit tests render JSX that calls `useTranslation()`; react-i18next
 * only resolves real copy once the default i18next instance has been
 * initialized via initReactI18next (done in ./index). Without importing the
 * bootstrap here, `t()` falls back to returning the raw key.
 *
 * Importing ./index in a node test environment is safe: storage access and
 * document mutations are guarded inside the module.
 */
import './index';
