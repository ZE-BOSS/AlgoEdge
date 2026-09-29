import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import { day, money } from '../lib/format';
import { color } from '../lib/theme';

/**
 * One dollar series over time: the investor's holding value, or before they hold
 * anything, what $100 invested at launch is worth. One series, so no legend — the
 * card title names it. `field` picks the series.
 * Touch and drag to read any day; "Show as table" gives the same numbers as a list.
 * Coordinates use Number(); every figure shown is the API's own string.
 */
const H = 180, P = { t: 10, r: 10, b: 24, l: 56 };

type Point = { date: string; nav_per_unit: string; value: string | null };

// axis labels only: short dollar figures ($1.2k, $950)
function axisMoney(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}m`;
  if (a >= 1e4) return `$${(v / 1e3).toFixed(0)}k`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(a < 10 ? 2 : 0)}`;
}

export default function NavChart({ points: all, field = 'value' }: { points: Point[]; field?: 'value' | 'nav_per_unit' }) {
  const points = (all || []).filter((p) => p[field] !== null && p[field] !== undefined);
  const val = (p: Point) => p[field] as string;
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);

  if (!points || points.length < 2) {
    return <Text style={{ color: color.text2 }}>The chart appears once the fund has been valued on two separate days.</Text>;
  }
  const ys = points.map((p) => Number(val(p)));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1e-9) { lo -= Math.max(1, hi * 0.01); hi += Math.max(1, hi * 0.01); }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const iw = w - P.l - P.r, ih = H - P.t - P.b;
  const x = (i: number) => P.l + (i / (points.length - 1)) * iw;
  const y = (v: number) => P.t + (1 - (v - lo) / (hi - lo)) * ih;
  const d = ys.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const ticks = [0, 0.5, 1].map((f) => lo + pad + f * (hi - lo - 2 * pad));
  const pick = (px: number) => setHover(Math.min(points.length - 1, Math.max(0, Math.round(((px - P.l) / iw) * (points.length - 1)))));
  const hp = hover != null ? points[hover] : null;

  return (
    <View style={{ gap: 8 }}>
      {table ? (
        <View>
          {[...points].reverse().slice(0, 60).map((p) => (
            <View key={p.date} style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}>
              <Text style={{ color: color.text2 }}>{day(p.date)}</Text>
              <Text style={{ color: color.text, fontFamily: 'monospace' }}>{money(val(p))}</Text>
            </View>
          ))}
        </View>
      ) : (
        <View onLayout={(e) => setW(Math.max(260, Math.floor(e.nativeEvent.layout.width)))}
          onStartShouldSetResponder={() => true} onMoveShouldSetResponder={() => true}
          onResponderGrant={(e) => pick(e.nativeEvent.locationX)} onResponderMove={(e) => pick(e.nativeEvent.locationX)}
          onResponderRelease={() => setHover(null)} onResponderTerminate={() => setHover(null)}
          accessibilityRole="image" accessibilityLabel={`Value in US dollars from ${day(points[0].date)} to ${day(points.at(-1)!.date)}, now ${money(val(points.at(-1)!))}`}>
          <Svg width={w} height={H}>
            {ticks.map((t) => (
              <Line key={`g${t}`} x1={P.l} x2={w - P.r} y1={y(t)} y2={y(t)} stroke={color.line} strokeWidth={1} />
            ))}
            {ticks.map((t) => (
              <SvgText key={`t${t}`} x={P.l - 8} y={y(t) + 4} fill={color.muted} fontSize={11} fontFamily="monospace" textAnchor="end">{axisMoney(t)}</SvgText>
            ))}
            <SvgText x={P.l} y={H - 6} fill={color.muted} fontSize={11} fontFamily="monospace">{day(points[0].date)}</SvgText>
            <SvgText x={w - P.r} y={H - 6} fill={color.muted} fontSize={11} fontFamily="monospace" textAnchor="end">{day(points.at(-1)!.date)}</SvgText>
            <Path d={d} stroke={color.gold} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
            {hp && <Line x1={x(hover!)} x2={x(hover!)} y1={P.t} y2={P.t + ih} stroke={color.muted} strokeDasharray="3 3" />}
            {hp && <Circle cx={x(hover!)} cy={y(ys[hover!])} r={5} fill={color.gold} stroke={color.surface} strokeWidth={2} />}
            <Circle cx={x(points.length - 1)} cy={y(ys.at(-1)!)} r={4} fill={color.gold} stroke={color.surface} strokeWidth={2} />
          </Svg>
          <Text style={{ color: color.text2, fontSize: 13, minHeight: 18 }}>
            {hp ? `${day(hp.date)} · ${money(val(hp))}` : 'Touch the chart to read a day'}
          </Text>
        </View>
      )}
      <Pressable onPress={() => setTable((t) => !t)} hitSlop={8} accessibilityRole="button">
        <Text style={{ color: color.gold, fontSize: 14 }}>{table ? 'Show as chart' : 'Show as table'}</Text>
      </Pressable>
    </View>
  );
}
