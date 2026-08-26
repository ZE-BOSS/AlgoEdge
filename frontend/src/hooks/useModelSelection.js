import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAnalysisModels } from '../services/api';

/**
 * Claude model / effort / output-ceiling selection.
 *
 * Lives in its own module rather than beside <ModelPicker> so that file exports
 * only a component — a mixed component+hook module breaks Vite's fast refresh
 * for everything that imports it.
 *
 * The catalogue is served by the backend (`/analysis/models`) instead of being
 * hardcoded here, so there is exactly one place a model id can be wrong and a
 * model added server-side appears in every picker at once.
 */

const STORAGE_KEY = 'algoedge_model_prefs_v1';

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch { return {}; }
}

function savePrefs(p) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(p)); } catch { /* quota — non-fatal */ }
}

export function useModelSelection() {
  const { data } = useQuery({
    queryKey: ['analysis-models'],
    queryFn: () => getAnalysisModels().then(r => r.data),
    staleTime: 5 * 60 * 1000,
  });

  const [prefs, setPrefs] = useState(loadPrefs);

  const models = useMemo(
    () => data?.providers?.[prefs.provider || 'anthropic']?.models || [],
    [data, prefs.provider],
  );

  const model = useMemo(() => {
    const wanted = prefs.model || data?.default_model;
    return models.find(m => m.id === wanted) || models[0] || null;
  }, [models, prefs.model, data?.default_model]);

  // Clamp whenever the selected model changes: a ceiling carried over from
  // Opus 5 (128K) would be rejected outright by Haiku 4.5 (64K).
  const resolvedMaxTokens = useMemo(() => {
    if (!model) return null;
    const ceiling = model.max_output;
    return prefs.maxTokens && prefs.maxTokens < ceiling ? prefs.maxTokens : ceiling;
  }, [model, prefs.maxTokens]);

  const rawEffort = prefs.effort || data?.default_effort || 'high';

  // null == "this model's real maximum" — the backend resolves it. Sending an
  // explicit number can only lower the ceiling, never raise it.
  const maxTokens = model && resolvedMaxTokens === model.max_output ? null : resolvedMaxTokens;
  // Only send effort where the model supports it; Haiku 4.5 errors on it.
  const effort = model?.supports_effort ? rawEffort : null;

  const update = (patch) => setPrefs(prev => {
    const next = { ...prev, ...patch };
    savePrefs(next);
    return next;
  });

  return {
    catalogue: data,
    models,
    model,
    provider: prefs.provider || 'anthropic',
    maxTokens,
    resolvedMaxTokens,
    effort,
    rawEffort,
    update,
    /** Spread straight into a runAnalysis() body. */
    requestFields: () => ({
      provider: prefs.provider || 'anthropic',
      model: model?.id,
      max_tokens: maxTokens,
      effort,
    }),
  };
}

export default useModelSelection;
