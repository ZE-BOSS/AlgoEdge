import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';
import { day, units } from '../lib/format';
import { color } from '../lib/theme';

/**
 * Price per unit over time. One series, so no legend — the card title names it.
 * Touch and drag to read any day; "Show as table" gives the same numbers as a list.
 * Coordinates use Number(); every figure shown is the API's own string.
 */
const H = 180, P = { t: 10, r: 10, b: 24, l: 50 };

export default function NavChart({ points }: { points: { date: string; nav_per_unit: string }[] }) {
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);

  if (!points || points.length < 2) {
    return <Text style={{ color: color.text2 }}>The chart appears once the fund has been priced on two separate days.</Text>;
  }
  const ys = points.map((p) => Number(p.nav_per_unit));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
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
              <Text style={{ color: color.text, fontFamily: 'monospace' }}>{units(p.nav_per_unit)}</Text>
            </View>
          ))}
        </View>
      ) : (
        <View onLayout={(e) => setW(Math.max(260, Math.floor(e.nativeEvent.layout.width)))}
          onStartShouldSetResponder={() => true} onMoveShouldSetResponder={() => true}
          onResponderGrant={(e) => pick(e.nativeEvent.locationX)} onResponderMove={(e) => pick(e.nativeEvent.locationX)}
          onResponderRelease={() => setHover(null)} onResponderTerminate={() => setHover(null)}
          accessibilityRole="image" accessibilityLabel={`Price per unit from ${day(points[0].date)} to ${day(points.at(-1)!.date)}, now ${units(points.at(-1)!.nav_per_unit)}`}>
          <Svg width={w} height={H}>
            {ticks.map((t) => (
              <Line key={`g${t}`} x1={P.l} x2={w - P.r} y1={y(t)} y2={y(t)} stroke={color.line} strokeWidth={1} />
            ))}
            {ticks.map((t) => (
              <SvgText key={`t${t}`} x={P.l - 8} y={y(t) + 4} fill={color.muted} fontSize={11} fontFamily="monospace" textAnchor="end">{t.toFixed(2)}</SvgText>
            ))}
            <SvgText x={P.l} y={H - 6} fill={color.muted} fontSize={11} fontFamily="monospace">{day(points[0].date)}</SvgText>
            <SvgText x={w - P.r} y={H - 6} fill={color.muted} fontSize={11} fontFamily="monospace" textAnchor="end">{day(points.at(-1)!.date)}</SvgText>
            <Path d={d} stroke={color.gold} strokeWidth={2} fill="none" strokeLinejoin="round" strokeLinecap="round" />
            {hp && <Line x1={x(hover!)} x2={x(hover!)} y1={P.t} y2={P.t + ih} stroke={color.muted} strokeDasharray="3 3" />}
            {hp && <Circle cx={x(hover!)} cy={y(ys[hover!])} r={5} fill={color.gold} stroke={color.surface} strokeWidth={2} />}
            <Circle cx={x(points.length - 1)} cy={y(ys.at(-1)!)} r={4} fill={color.gold} stroke={color.surface} strokeWidth={2} />
          </Svg>
          <Text style={{ color: color.text2, fontSize: 13, minHeight: 18 }}>
            {hp ? `${day(hp.date)} · ${units(hp.nav_per_unit)}` : 'Touch the chart to read a day'}
          </Text>
        </View>
      )}
      <Pressable onPress={() => setTable((t) => !t)} hitSlop={8} accessibilityRole="button">
        <Text style={{ color: color.gold, fontSize: 14 }}>{table ? 'Show as chart' : 'Show as table'}</Text>
      </Pressable>
    </View>
  );
}
