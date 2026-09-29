import { useState, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
  type TextInputProps } from 'react-native';
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

export const s = StyleSheet.create({
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
