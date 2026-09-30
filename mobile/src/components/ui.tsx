import { useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
  type TextInputProps } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { compact, money } from '../lib/format';
import { usePrefs } from '../lib/prefs';
import { color, mono } from '../lib/theme';

export function Screen({ children, onRefresh }: { children: ReactNode; onRefresh?: () => void }) {
  const [spinning, setSpinning] = useState(false);
  return (
    <ScrollView style={s.screen} contentContainerStyle={s.screenBody} keyboardShouldPersistTaps="handled"
      refreshControl={onRefresh ? (
        <RefreshControl refreshing={spinning} tintColor={color.gold} colors={[color.gold]}
          onRefresh={() => { setSpinning(true); onRefresh(); setTimeout(() => setSpinning(false), 600); }} />
      ) : undefined}>
      {children}
    </ScrollView>
  );
}

export function Card({ title, right, children }: { title?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <View style={s.card}>
      {(title || right) && (
        <View style={s.cardHead}>
          {title ? <Text style={s.cardTitle}>{title.toUpperCase()}</Text> : <View />}
          {right}
        </View>
      )}
      {children}
    </View>
  );
}

export function T({ children, style, tone, size, mono: m, bold }: {
  children: ReactNode; style?: object; tone?: 'muted' | 'good' | 'bad' | 'gold' | 'text2';
  size?: number; mono?: boolean; bold?: boolean;
}) {
  const c = tone === 'muted' ? color.muted : tone === 'good' ? color.good : tone === 'bad' ? color.bad
    : tone === 'gold' ? color.gold : tone === 'text2' ? color.text2 : color.text;
  return (
    <Text style={[{ color: c, fontSize: size ?? 15, lineHeight: (size ?? 15) * 1.45 },
      m && { fontFamily: mono }, bold && { fontWeight: '700' }, style]}>{children}</Text>
  );
}

export function Button({ label, onPress, kind = 'plain', busy, disabled }: {
  label: string; onPress: () => void; kind?: 'primary' | 'plain' | 'danger' | 'link'; busy?: boolean; disabled?: boolean;
}) {
  const off = disabled || busy;
  if (kind === 'link') {
    return <Pressable onPress={onPress} disabled={off} hitSlop={10} accessibilityRole="link">
      <Text style={{ color: color.gold, fontSize: 15, opacity: off ? 0.5 : 1 }}>{label}</Text></Pressable>;
  }
  return (
    <Pressable onPress={onPress} disabled={off} accessibilityRole="button" accessibilityState={{ disabled: !!off }}
      style={({ pressed }) => [s.btn, kind === 'primary' && s.btnPrimary, kind === 'danger' && s.btnDanger,
        (pressed || off) && { opacity: off ? 0.5 : 0.8 }]}>
      {busy ? <ActivityIndicator color={kind === 'primary' ? color.goldInk : color.text} />
        : <Text style={[s.btnText, kind === 'primary' && { color: color.goldInk }, kind === 'danger' && { color: '#1b0a0a' }]}>{label}</Text>}
    </Pressable>
  );
}

export function Field({ label, hint, ...props }: TextInputProps & { label: string; hint?: string }) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={s.label}>{label}</Text>
      <TextInput placeholderTextColor={color.muted} style={[s.input, props.multiline && { minHeight: 80, textAlignVertical: 'top' }]} {...props} />
      {hint ? <Text style={s.hint}>{hint}</Text> : null}
    </View>
  );
}

/** A password field with Show / Hide, so a typo can be seen before it is sent. */
export function PasswordField({ label, hint, ...props }: TextInputProps & { label: string; hint?: string }) {
  const [shown, setShown] = useState(false);
  return (
    <View style={{ gap: 6 }}>
      <Text style={s.label}>{label}</Text>
      <View style={{ justifyContent: 'center' }}>
        <TextInput placeholderTextColor={color.muted} style={[s.input, { paddingRight: 72 }]}
          autoCapitalize="none" autoCorrect={false} {...props} secureTextEntry={!shown} />
        <Pressable onPress={() => setShown((v) => !v)} hitSlop={8} style={s.eye}
          accessibilityRole="button" accessibilityLabel={shown ? 'Hide password' : 'Show password'}>
          <Text style={{ color: color.gold, fontWeight: '600', fontSize: 14 }}>{shown ? 'Hide' : 'Show'}</Text>
        </Pressable>
      </View>
      {hint ? <Text style={s.hint}>{hint}</Text> : null}
    </View>
  );
}

export function Problem({ error }: { error?: Error | null }) {
  if (!error) return null;
  return <Text style={{ color: color.bad, fontSize: 14 }} accessibilityRole="alert">{error.message}</Text>;
}

export function Loaded<D>({ q, children }: { q: { data: D | null; error: Error | null; loading: boolean; reload: () => void }; children: (d: D) => ReactNode }) {
  if (q.loading && q.data === null) return <ActivityIndicator color={color.gold} style={{ marginVertical: 24 }} />;
  if (q.error && q.data === null) {
    return <View style={{ gap: 10 }}><Problem error={q.error} /><Button label="Try again" kind="link" onPress={q.reload} /></View>;
  }
  return <>{children(q.data as D)}</>;
}

export function Pairs({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <View style={{ gap: 8 }}>
      {rows.map(([k, v]) => (
        <View key={k} style={s.pair}>
          <Text style={s.pairKey}>{k}</Text>
          {typeof v === 'string'
            // figures in the tabular monospace; words (a bank, a name) in the body face
            ? <Text style={[s.pairVal, /^[-−+$\d.,%·\s]+$/.test(v) && { fontFamily: mono }]}>{v}</Text>
            : v}
        </View>
      ))}
    </View>
  );
}

export function Badge({ label, tone = 'neutral' }: { label: string; tone?: 'good' | 'bad' | 'info' | 'neutral' }) {
  const c = tone === 'good' ? color.good : tone === 'bad' ? color.bad : tone === 'info' ? color.info : color.text2;
  return <View style={[s.badge, { borderColor: c + '66' }]}><Text style={{ color: c, fontSize: 11, fontWeight: '700' }}>{label}</Text></View>;
}

export function Segmented<K extends string>({ value, options, onChange }: { value: K; options: [K, string][]; onChange: (k: K) => void }) {
  return (
    <View style={s.seg}>
      {options.map(([k, label]) => (
        <Pressable key={k} onPress={() => onChange(k)} style={[s.segItem, value === k && s.segOn]} accessibilityRole="tab"
          accessibilityState={{ selected: value === k }}>
          <Text style={{ color: value === k ? color.text : color.text2, fontWeight: '600' }}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

/** Line icons on a 24px grid, the same set as the web dashboard. */
const ICONS: Record<string, string> = {
  home: 'M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z',
  money: 'M3 7h18v10H3zM7 12h.01M17 12h.01M12 14.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  trades: 'M4 19V9M10 19V5M16 19v-7M22 19H2',
  settings: 'M12 15a3 3 0 100-6 3 3 0 000 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z',
  eyeOff: 'M3 3l18 18M10.6 5.1A9.7 9.7 0 0112 5c6.5 0 10 7 10 7a17 17 0 01-3 3.7M6.6 6.6A17 17 0 002 12s3.5 7 10 7a9.6 9.6 0 004.9-1.3M9.9 9.9a3 3 0 004.2 4.2',
  arrowIn: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  arrowOut: 'M12 21V9M7 14l5-5 5 5M5 3h14',
  fee: 'M19 5L5 19M7.5 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM16.5 19a1.5 1.5 0 100-3 1.5 1.5 0 000 3z',
  fix: 'M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4L15 12l-3-3z',
  up: 'M4 16l6-6 4 4 6-7M14 7h6v6',
  down: 'M4 8l6 6 4-4 6 7M14 17h6v-6',
  chart: 'M3 3v18h18M7 14l4-4 3 3 5-6',
  info: 'M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-4M12 8h.01',
  user: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0',
  bell: 'M6 8a6 6 0 1112 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4',
  shield: 'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z',
  bank: 'M3 10l9-6 9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 21h18',
  doc: 'M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9zM14 3v6h6M8 13h8M8 17h5',
};

export function Icon({ name, size = 20, tint = color.text2 }: { name: string; size?: number; tint?: string }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={tint} strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round"><Path d={ICONS[name] || ICONS.info} /></Svg>
  );
}

/** A dollar amount that honours "hide balances" and "short numbers". */
export function Amount({ v, sign, tone, size, bold, full }: {
  v: string | null | undefined; sign?: boolean; tone?: 'muted' | 'good' | 'bad' | 'gold' | 'text2';
  size?: number; bold?: boolean; full?: boolean;
}) {
  const { hidden, prefs } = usePrefs();
  if (hidden) return <T tone="muted" size={size} bold={bold}>••••••</T>;
  let text = prefs.compact_numbers && !full ? compact(v) : money(v, { sign });
  if (sign && prefs.compact_numbers && !full && Number(v) > 0) text = `+${text}`;
  return <T tone={tone} size={size} bold={bold} mono>{text}</T>;
}

/** A tile: small label with an icon, a figure, an optional note. */
export function Stat({ label, icon, children, note, tone }: {
  label: string; icon?: string; children: ReactNode; note?: ReactNode; tone?: 'good' | 'bad';
}) {
  return (
    <View style={s.stat}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        {icon ? <Icon name={icon} size={14} tint={color.muted} /> : null}
        <Text style={{ color: color.muted, fontSize: 12 }}>{label}</Text>
      </View>
      {typeof children === 'string' || typeof children === 'number'
        ? <T bold size={17} tone={tone}>{children}</T> : children}
      {note ? <Text style={{ color: color.muted, fontSize: 11 }}>{note}</Text> : null}
    </View>
  );
}

/** Buy / Sell tag for a trade. */
export function Direction({ d }: { d: string }) {
  const buy = d === 'BUY';
  return (
    <View style={{ width: 40, paddingVertical: 4, borderRadius: 8, alignItems: 'center',
      backgroundColor: (buy ? color.good : color.bad) + '22' }}>
      <T size={11} bold tone={buy ? 'good' : 'bad'}>{buy ? 'Buy' : 'Sell'}</T>
    </View>
  );
}

/** Filter chips in a row. */
export function Chips<K extends string>({ value, options, onChange }: { value: K; options: [K, string][]; onChange: (k: K) => void }) {
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {options.map(([k, label]) => (
        <Pressable key={k} onPress={() => onChange(k)} accessibilityRole="button" accessibilityState={{ selected: value === k }}
          style={{ paddingHorizontal: 14, paddingVertical: 7, borderRadius: 99, borderWidth: 1,
            borderColor: value === k ? color.gold : color.line, backgroundColor: value === k ? color.gold + '22' : color.surface }}>
          <Text style={{ color: value === k ? color.gold : color.text2, fontSize: 13, fontWeight: '600' }}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export const s = StyleSheet.create({
  stat: { flexBasis: '47%', flexGrow: 1, backgroundColor: color.surface, borderColor: color.line, borderWidth: 1,
    borderRadius: 14, padding: 12, gap: 4 },
  screen: { flex: 1, backgroundColor: color.bg },
  screenBody: { padding: 16, paddingBottom: 40, gap: 16 },
  card: { backgroundColor: color.surface, borderColor: color.line, borderWidth: 1, borderRadius: 14, padding: 16, gap: 10 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardTitle: { color: color.text2, fontSize: 12, fontWeight: '700', letterSpacing: 1.2 },
  btn: { minHeight: 48, borderRadius: 12, borderWidth: 1, borderColor: color.line, backgroundColor: color.surface2,
    alignItems: 'center', justifyContent: 'center', paddingHorizontal: 16 },
  btnPrimary: { backgroundColor: color.gold, borderColor: color.gold },
  btnDanger: { backgroundColor: color.bad, borderColor: color.bad },
  btnText: { color: color.text, fontSize: 16, fontWeight: '700' },
  label: { color: color.text2, fontSize: 14 },
  hint: { color: color.muted, fontSize: 12 },
  input: { backgroundColor: color.bg, borderColor: color.line, borderWidth: 1, borderRadius: 12, color: color.text,
    fontSize: 16, paddingHorizontal: 14, paddingVertical: 12 },
  eye: { position: 'absolute', right: 0, top: 0, bottom: 0, paddingHorizontal: 16, justifyContent: 'center' },
  pair: { flexDirection: 'row', justifyContent: 'space-between', gap: 16 },
  pairKey: { color: color.text2, fontSize: 15, flexShrink: 1 },
  pairVal: { color: color.text, fontSize: 15, textAlign: 'right', flexShrink: 1 },
  badge: { borderWidth: 1, borderRadius: 99, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start' },
  seg: { flexDirection: 'row', backgroundColor: color.surface, borderColor: color.line, borderWidth: 1, borderRadius: 12, padding: 4 },
  segItem: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 9 },
  segOn: { backgroundColor: color.surface2 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, paddingVertical: 12,
    borderBottomColor: color.line, borderBottomWidth: StyleSheet.hairlineWidth },
});
