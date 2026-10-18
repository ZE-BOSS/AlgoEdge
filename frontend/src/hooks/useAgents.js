/**
 * frontend/src/hooks/useAgents.js
 *
 * The two queries the Backtester needs about RL agents, in their own file
 * because a file that exports both components and hooks breaks fast refresh
 * (and the project's lint says so).
 */

import { useQuery } from '@tanstack/react-query';
import { STRATEGY_OPTIONS } from '../components/slotSpec';
import { rl } from '../services/api';
import { qk } from '../services/servers';

/** The schema group an approved agent sits under in a strategy list. */
export const AGENT_GROUP = 'rl';

/**
 * Every strategy that can be RUN: the rule-based list plus each approved agent.
 *
 * Same `[id, label, group]` shape as `STRATEGY_OPTIONS`, so every picker - the
 * Backtester, a portfolio row, a live slot, Compare - lists agents exactly
 * where it lists strategies. Until 2026-10-17 the pickers read the static list
 * only: an approved agent could be added as a SUPERVISOR but never selected to
 * trade on its own, and the Backtester quietly swapped an unknown id for
 * ORB_v1. The backend always accepted it - approved agents are registered in
 * the same strategy registry the engine and the live bot read.
 *
 * Agents that failed the validation gate are still listed (the admin approved
 * them deliberately) but labelled, because "approved" and "passed" are not the
 * same thing and a picker should not hide the difference.
 */
export function useStrategyOptions() {
  const { data: agents = [] } = useApprovedAgents();
  const agentOptions = agents.map((a) => [
    a.strategy_id,
    `Agent: ${a.name} v${a.charter_version ?? '?'}`
      + (a.validation_passed === false ? ' (failed gate)' : ''),
    AGENT_GROUP,
  ]);
  return [...STRATEGY_OPTIONS, ...agentOptions];
}

/** `RL::name@3` is an agent's strategy id; everything else is rule-based. */
export const isAgentStrategy = (id) => String(id || '').startsWith('RL::');

/**
 * Agents that can actually be selected: an APPROVED charter and a strategy id.
 *
 * Filtered rather than listing everything, because only approved agents are
 * registered as strategies - an unapproved one in a picker would be a control
 * that errors when used, which is worse than one that is absent.
 */
export function useApprovedAgents() {
  return useQuery({
    queryKey: qk('rl-approved'),
    queryFn: () => rl.list().then((r) => (r.data?.agents || []).filter(
      (a) => a.strategy_id && a.charter_status === 'APPROVED')),
    staleTime: 60000,
  });
}

/**
 * Whether a backtest window is inside what an agent trained on.
 *
 * A hook rather than a component with a callback, deliberately: the caller
 * needs the verdict for TWO things - the banner and whether Run is clickable -
 * and reporting state back through a callback meant a setState inside an
 * effect, which cascades renders. One query, one source of truth, read twice.
 */
export function useOverlap({ agentId, symbol, timeframe = 'M5', start, end }) {
  const enabled = Boolean(agentId && symbol && start && end);
  const { data } = useQuery({
    queryKey: qk('rl-overlap', agentId, symbol, timeframe, start, end),
    queryFn: () => rl.overlap({ agent_id: agentId, symbol, timeframe, start, end })
      .then((r) => r.data),
    enabled,
    // A window's overlap with a frozen training range does not change.
    staleTime: Infinity,
    retry: false,
  });
  return enabled ? (data || null) : null;
}

/**
 * `RL::name@3` -> the agent_id the overlap endpoint keys by.
 *
 * The strategy id is what the registry and the slot editor use; the agent id is
 * what the database row is keyed by, and they are deliberately different - an
 * agent keeps one id across every version of its charter. Looked up from the
 * list rather than parsed out of the strategy id, because a name is not an id
 * and two agents may share a name.
 */
export function useAgentIdLookup() {
  const { data: agents = [] } = useApprovedAgents();
  return (strategyId) => (agents.find((a) => a.strategy_id === strategyId) || {}).agent_id
    || null;
}

/**
 * The rule-based strategies a mode-B agent can be built on.
 *
 * Agents are excluded by the endpoint: an agent optimising another agent is
 * not something this design supports, and offering it would invite a loop
 * nobody can reason about.
 */
export function useBaseStrategies() {
  const { data } = useQuery({
    queryKey: qk('rl-base-strategies'),
    queryFn: () => rl.strategies().then((r) => r.data?.strategies || []),
    staleTime: 600000,
  });
  return data || [];
}
