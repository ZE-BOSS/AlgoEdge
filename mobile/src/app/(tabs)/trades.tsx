import { useState } from 'react';
import { View } from 'react-native';
import { api, type Trade } from '../../lib/api';
import { day, isNeg, pct } from '../../lib/format';
import { useLoad } from '../../lib/hooks';
import { color } from '../../lib/theme';
import { SymbolBars } from '../../components/Charts';
import { Amount, Card, Chips, Direction, Loaded, Screen, Stat, T, s } from '../../components/ui';

type Filter = 'all' | 'mine' | 'wins' | 'losses';
const FILTERS: [Filter, string][] = [['all', 'All'], ['mine', 'While invested'], ['wins', 'Gains'], ['losses', 'Losses']];

function keep(t: Trade, f: Filter, market: string) {
  if (market !== 'all' && t.symbol !== market) return false;
  if (f === 'mine') return t.your_amount !== null;
  if (f === 'wins') return t.your_amount !== null && Number(t.your_amount) > 0;
  if (f === 'losses') return t.your_amount !== null && Number(t.your_amount) < 0;
  return true;
}

export default function Trades() {
  const q = useLoad(api.trades);
  const [filter, setFilter] = useState<Filter>('all');
  const [market, setMarket] = useState('all');
  return (
    <Screen onRefresh={q.reload}>
      <Loaded q={q}>
        {({ trades, summary: sm }) => {
          const markets = [...new Set(trades.map((t) => t.symbol))].sort();
          const rows = trades.filter((t) => keep(t, filter, market));
          return (
            <>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
                <Stat label="Your total from trades" icon="chart">
                  <Amount v={sm.your_total} sign bold size={17} tone={isNeg(sm.your_total) ? 'bad' : 'good'} /></Stat>
                <Stat label="Win rate" icon="up" note={`${sm.wins} gains · ${sm.losses} losses`}>
                  {sm.win_rate_pct ? `${sm.win_rate_pct}%` : '—'}</Stat>
              </View>

              <Card title="How your share is worked out">
                <T tone="text2" size={13}>When a trade is published, its result is split between investors by the units each
                  held at the end of the day before it closed. You see only your own share; a trade that closed before your
                  money arrived shows as before you joined.</T>
              </Card>

              {sm.by_symbol.length > 0 && (
                <Card title="Your result by market"><SymbolBars rows={sm.by_symbol} /></Card>
              )}

              <Card title="Closed trades">
                <Chips value={filter} options={FILTERS} onChange={setFilter} />
                {markets.length > 1 && (
                  <Chips value={market} options={[['all', 'Every market'], ...markets.map((m) => [m, m] as [string, string])]}
                    onChange={setMarket} />
                )}
                {trades.length === 0 ? <T tone="text2">No trades have been published yet.</T>
                  : rows.length === 0 ? <T tone="text2">No trades match these filters.</T> : (
                    <View>
                      {rows.map((t) => (
                        <View key={t.id} style={[s.row, { alignItems: 'center' }]}>
                          <Direction d={t.direction} />
                          <View style={{ flex: 1, gap: 2 }}>
                            <T bold>{t.symbol}</T>
                            <T tone="muted" size={12}>{day(t.closed_on)}{t.result_pct ? ` · fund ${pct(t.result_pct)}` : ''}</T>
                            {t.note ? <T size={12} tone="text2">{t.note}</T> : null}
                          </View>
                          <View style={{ alignItems: 'flex-end', gap: 2 }}>
                            {t.your_amount === null ? <T tone="muted" size={12}>before you joined</T> : (
                              <>
                                <Amount v={t.your_amount} sign bold tone={isNeg(t.your_amount) ? 'bad' : 'good'} />
                                {t.your_share_pct ? <T tone="muted" size={11}>your share {t.your_share_pct}%</T> : null}
                              </>
                            )}
                          </View>
                        </View>
                      ))}
                    </View>
                  )}
              </Card>
              <View style={{ height: 1, backgroundColor: color.bg }} />
            </>
          );
        }}
      </Loaded>
    </Screen>
  );
}
