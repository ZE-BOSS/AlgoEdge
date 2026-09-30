import { useMemo, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import type { Month, NavPoint } from '../lib/api';
import { compact, day, money, monthName } from '../lib/format';
import { usePrefs } from '../lib/prefs';
import { color } from '../lib/theme';
import { T } from './ui';

// Validated pair against the card surface (dark): balance and money put in.
// Gain and loss use the reserved good/bad colours plus a sign and a label.
const BAL = '#b08a1a';
const INV = '#4a86d8';
const RANGES: [string, number | null][] = [['1M', 31], ['3M', 92], ['6M', 183], ['1Y', 366], ['ALL', null]];

export function RangeTabs({ value, onChange }: { value: string; onChange: (k: string) => void }) {
  return (
    <View style={{ flexDirection: 'row', backgroundColor: color.bg, borderColor: color.line, borderWidth: 1, borderRadius: 10, padding: 3 }}>
      {RANGES.map(([k]) => (
        <Pressable key={k} onPress={() => onChange(k)} accessibilityRole="tab" accessibilityState={{ selected: value === k }}
          style={{ paddingHorizontal: 10, paddingVertical: 5, borderRadius: 7, backgroundColor: value === k ? color.surface2 : 'transparent' }}>
          <Text style={{ color: value === k ? color.gold : color.muted, fontSize: 12, fontWeight: '700' }}>{k === 'ALL' ? 'All' : k}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function Key({ c, label }: { c: string; label: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: c }} />
      <Text style={{ color: color.text2, fontSize: 12 }}>{label}</Text>
    </View>
  );
}

/** Balance against money put in; the gap is profit (green) or capital lost (red). */
export function BalanceChart({ points: all, range, onRange }: { points: NavPoint[]; range: string; onRange: (r: string) => void }) {
  const { hidden } = usePrefs();
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const mine = useMemo(() => all.filter((p) => p.value !== null), [all]);
  const points = useMemo(() => {
    const days = RANGES.find(([k]) => k === range)?.[1];
    if (!days || mine.length === 0) return mine;
    const last = Date.parse(`${mine[mine.length - 1].date}T12:00:00Z`);
    return mine.filter((p) => last - Date.parse(`${p.date}T12:00:00Z`) <= days * 864e5);
  }, [mine, range]);
  if (mine.length < 2) {
    return <T tone="text2">Your balance chart starts once the fund has been valued on two days since your money arrived.</T>;
  }
  const H = 200, P = { t: 10, r: 8, b: 22, l: 52 };
  const val = points.map((p) => Number(p.value));
  const inv = points.map((p) => Number(p.net_invested));
  let lo = Math.min(...val, ...inv), hi = Math.max(...val, ...inv);
  if (hi - lo < 1e-6) { lo -= Math.max(1, hi * 0.02); hi += Math.max(1, hi * 0.02); }
  const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
  const iw = w - P.l - P.r, ih = H - P.t - P.b, n = Math.max(1, points.length - 1);
  const x = (i: number) => P.l + (i / n) * iw;
  const y = (v: number) => P.t + (1 - (v - lo) / (hi - lo)) * ih;
  const line = (a: number[]) => a.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const gaps: { d: string; good: boolean }[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const d0 = val[i] - inv[i], d1 = val[i + 1] - inv[i + 1];
    const seg = (ai: number, av: number, au: number, bi: number, bv: number, bu: number, good: boolean) =>
      gaps.push({ good, d: `M${x(ai)},${y(av)}L${x(bi)},${y(bv)}L${x(bi)},${y(bu)}L${x(ai)},${y(au)}Z` });
    if ((d0 >= 0) === (d1 >= 0)) seg(i, val[i], inv[i], i + 1, val[i + 1], inv[i + 1], d0 + d1 >= 0);
    else {
      const t = d0 / (d0 - d1), ci = i + t;
      const cv = val[i] + t * (val[i + 1] - val[i]), cu = inv[i] + t * (inv[i + 1] - inv[i]);
      seg(i, val[i], inv[i], ci, cv, cu, d0 >= 0); seg(ci, cv, cu, i + 1, val[i + 1], inv[i + 1], d1 >= 0);
    }
  }
  const pick = (px: number) => setHover(Math.min(points.length - 1, Math.max(0, Math.round(((px - P.l) / iw) * n))));
  const hp = hover != null ? points[hover] : null;
  const diff = hp ? Number(hp.value) - Number(hp.net_invested) : 0;
  const ticks = [0, 0.5, 1].map((f) => lo + pad + f * (hi - lo - 2 * pad));
  return (
    <View style={{ gap: 10 }}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
        <Key c={BAL} label="Your balance" /><Key c={INV} label="Money you put in" />
        <Key c={color.good} label="Profit" /><Key c={color.bad} label="Capital lost" />
      </View>
      <RangeTabs value={range} onChange={onRange} />
      <View onLayout={(e) => setW(Math.max(260, Math.floor(e.nativeEvent.layout.width)))}
        onStartShouldSetResponder={() => true} onMoveShouldSetResponder={() => true}
        onResponderGrant={(e) => pick(e.nativeEvent.locationX)} onResponderMove={(e) => pick(e.nativeEvent.locationX)}
        onResponderRelease={() => setHover(null)} onResponderTerminate={() => setHover(null)}
        accessibilityRole="image" accessibilityLabel="Your balance against the money you put in">
        <Svg width={w} height={H}>
          {ticks.map((t) => <Line key={`g${t}`} x1={P.l} x2={w - P.r} y1={y(t)} y2={y(t)} stroke={color.line} strokeWidth={1} />)}
          {ticks.map((t) => (
            <SvgText key={`t${t}`} x={P.l - 6} y={y(t) + 4} fill={color.muted} fontSize={10} textAnchor="end">{hidden ? '•••' : compact(t)}</SvgText>
          ))}
          {gaps.map((g, i) => <Path key={i} d={g.d} fill={g.good ? color.good : color.bad} opacity={g.good ? 0.16 : 0.2} />)}
          <Path d={line(inv)} stroke={INV} strokeWidth={2} fill="none" strokeLinejoin="round" />
          <Path d={line(val)} stroke={BAL} strokeWidth={2} fill="none" strokeLinejoin="round" />
          <SvgText x={P.l} y={H - 5} fill={color.muted} fontSize={10}>{day(points[0].date)}</SvgText>
          <SvgText x={w - P.r} y={H - 5} fill={color.muted} fontSize={10} textAnchor="end">{day(points[points.length - 1].date)}</SvgText>
          {hp && <Line x1={x(hover!)} x2={x(hover!)} y1={P.t} y2={P.t + ih} stroke={color.muted} strokeDasharray="3 3" />}
          {hp && <Circle cx={x(hover!)} cy={y(val[hover!])} r={5} fill={BAL} stroke={color.surface} strokeWidth={2} />}
          <Circle cx={x(points.length - 1)} cy={y(val[val.length - 1])} r={4} fill={BAL} stroke={color.surface} strokeWidth={2} />
        </Svg>
      </View>
      <View style={{ minHeight: 40, gap: 2 }}>
        {hp ? (
          <>
            <T tone="muted" size={12}>{day(hp.date)}</T>
            <T size={13}>Balance {hidden ? '••••' : money(hp.value)} · put in {hidden ? '••••' : money(hp.net_invested)}</T>
            <T size={13} tone={diff < 0 ? 'bad' : 'good'}>{diff < 0 ? 'Capital lost' : 'Profit'} {hidden ? '••••'
              : `${diff < 0 ? '−' : '+'}${money(Math.abs(diff).toFixed(2))}`}</T>
          </>
        ) : <T tone="muted" size={12}>Touch the chart to read any day</T>}
      </View>
    </View>
  );
}

/** Profit or loss in dollars, month by month; tap a bar for the sum behind it. */
export function MonthBars({ months }: { months: Month[] }) {
  const { hidden } = usePrefs();
  const [w, setW] = useState(320);
  const [sel, setSel] = useState<number | null>(null);
  const list = months.slice(-12);
  if (!list.length) return <T tone="text2">Your first month appears once your money has been in for a month end.</T>;
  const H = 170, P = { t: 14, b: 22 };
  const vals = list.map((m) => Number(m.profit));
  const max = Math.max(1, ...vals.map(Math.abs));
  const mid = P.t + (H - P.t - P.b) / 2, half = (H - P.t - P.b) / 2 - 10;
  const bw = w / list.length;
  const m = sel != null ? list[sel] : null;
  return (
    <View style={{ gap: 8 }}>
      <View onLayout={(e) => setW(Math.max(260, Math.floor(e.nativeEvent.layout.width)))}
        onStartShouldSetResponder={() => true}
        onResponderGrant={(e) => setSel(Math.min(list.length - 1, Math.max(0, Math.floor(e.nativeEvent.locationX / bw))))}
        accessibilityRole="image" accessibilityLabel="Your profit or loss each month">
        <Svg width={w} height={H}>
          <Line x1={0} x2={w} y1={mid} y2={mid} stroke={color.muted} strokeWidth={1} />
          {list.map((mo, i) => {
            const v = vals[i], h = Math.max(2, (Math.abs(v) / max) * half);
            const cx = i * bw + bw / 2, bwid = Math.min(26, bw * 0.6);
            return (
              <Path key={mo.month} opacity={sel == null || sel === i ? 1 : 0.45} fill={v < 0 ? color.bad : color.good}
                d={v < 0 ? `M${cx - bwid / 2},${mid}h${bwid}v${h}h-${bwid}Z` : `M${cx - bwid / 2},${mid}v-${h}h${bwid}v${h}Z`} />
            );
          })}
          {list.map((mo, i) => (
            <SvgText key={`l${mo.month}`} x={i * bw + bw / 2} y={H - 5} fill={color.muted} fontSize={10} textAnchor="middle">
              {monthName(mo.month, true).split(' ')[0]}</SvgText>
          ))}
        </Svg>
      </View>
      {m ? (
        <View style={{ gap: 2 }}>
          <T bold size={13}>{monthName(m.month)}</T>
          <T size={13} tone="text2">Start {hidden ? '••••' : money(m.start_value)} · in/out {hidden ? '••••' : money(m.money_in_out, { sign: true })}
            {' '}· end {hidden ? '••••' : money(m.end_value)}</T>
          <T size={13} tone={Number(m.profit) < 0 ? 'bad' : 'good'}>Result {hidden ? '••••' : money(m.profit, { sign: true })}
            {m.fund_return_pct ? ` (fund ${Number(m.fund_return_pct) > 0 ? '+' : ''}${m.fund_return_pct}%)` : ''}</T>
        </View>
      ) : <T tone="muted" size={12}>Tap a month for the numbers behind it</T>}
    </View>
  );
}

/** Capital still there, and profit on top of it or capital lost. Labelled. */
export function CapitalBar({ c }: { c: any }) {
  const { hidden } = usePrefs();
  const cap = Number(c.capital_intact), prof = Number(c.profit_on_top), lost = Number(c.capital_eroded);
  const whole = cap + prof + lost || 1;
  const h = (v: string) => (hidden ? '••••' : money(v));
  return (
    <View style={{ gap: 10 }}>
      <View style={{ flexDirection: 'row', height: 16, borderRadius: 8, overflow: 'hidden', gap: 2, backgroundColor: color.bg }}>
        {cap > 0 && <View style={{ flex: cap / whole, backgroundColor: INV }} />}
        {prof > 0 && <View style={{ flex: prof / whole, backgroundColor: color.good }} />}
        {lost > 0 && <View style={{ flex: lost / whole, backgroundColor: color.bad, opacity: 0.7 }} />}
      </View>
      <View style={{ gap: 4 }}>
        <Key c={INV} label={`Your capital  ${h(c.capital_intact)}`} />
        {prof > 0 && <Key c={color.good} label={`Profit on top  +${h(c.profit_on_top)}`} />}
        {lost > 0 && <Key c={color.bad} label={`Capital lost  −${h(c.capital_eroded)}`} />}
      </View>
    </View>
  );
}

/** Result by market: bars out from zero, each value labelled. */
export function SymbolBars({ rows }: { rows: { symbol: string; amount: string }[] }) {
  const { hidden } = usePrefs();
  if (!rows.length) return null;
  const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r.amount))));
  return (
    <View style={{ gap: 8 }}>
      {rows.slice(0, 8).map((r) => {
        const v = Number(r.amount), frac = Math.abs(v) / max / 2;
        return (
          <View key={r.symbol} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: color.text, width: 78, fontWeight: '600', fontSize: 13 }} numberOfLines={1}>{r.symbol}</Text>
            <View style={{ flex: 1, height: 10, flexDirection: 'row' }}>
              <View style={{ flex: 0.5, flexDirection: 'row', justifyContent: 'flex-end' }}>
                {v < 0 && <View style={{ width: `${frac * 200}%`, backgroundColor: color.bad, borderTopLeftRadius: 4, borderBottomLeftRadius: 4 }} />}
              </View>
              <View style={{ width: 1, backgroundColor: color.muted }} />
              <View style={{ flex: 0.5, flexDirection: 'row' }}>
                {v > 0 && <View style={{ width: `${frac * 200}%`, backgroundColor: color.good, borderTopRightRadius: 4, borderBottomRightRadius: 4 }} />}
              </View>
            </View>
            <T mono size={12} tone={v < 0 ? 'bad' : 'good'}>{hidden ? '••••' : money(r.amount, { sign: true })}</T>
          </View>
        );
      })}
    </View>
  );
}
