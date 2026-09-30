import { useEffect } from 'react';
import { AppState, View } from 'react-native';
import { api } from '../lib/api';
import { ago } from '../lib/format';
import { useLoad } from '../lib/hooks';
import { color } from '../lib/theme';
import { Card, Direction, T, s } from './ui';

/** Trades open right now: market, side and when. Refreshes every minute while the app is open. */
export default function LiveTrades({ compact = false }: { compact?: boolean }) {
  const q = useLoad(api.live);
  const { reload } = q;
  useEffect(() => {
    const id = setInterval(() => { if (AppState.currentState === 'active') reload(); }, 60_000);
    return () => clearInterval(id);
  }, [reload]);
  const d = q.data;
  if (!d || !d.invested || (compact && d.trades.length === 0)) return null;
  return (
    <Card title="Live now" right={<View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: color.good }} />}>
      {d.trades.length === 0
        ? <T tone="text2" size={14}>No trades open right now. You get a notification the moment one opens.</T>
        : (
          <View>
            {d.trades.map((t) => (
              <View key={t.id} style={[s.row, { alignItems: 'center' }]}>
                <Direction d={t.direction} />
                <View style={{ flex: 1 }}>
                  <T bold>{t.symbol}</T>
                  <T tone="muted" size={12}>opened {ago(t.opened_at)}</T>
                </View>
                <View style={{ borderWidth: 1, borderColor: color.good + '77', borderRadius: 99, paddingHorizontal: 8, paddingVertical: 2 }}>
                  <T tone="good" size={11} bold>OPEN</T>
                </View>
              </View>
            ))}
            <T tone="muted" size={12}>Your share of each result reaches your balance when the trade is published after it closes.</T>
          </View>
        )}
    </Card>
  );
}
