import { useState, type ReactNode } from 'react';
import { Switch, View } from 'react-native';
import { router } from 'expo-router';
import * as Application from 'expo-application';
import { api, type Prefs } from '../../lib/api';
import { day, money } from '../../lib/format';
import { useLoad, useSubmit } from '../../lib/hooks';
import { usePrefs } from '../../lib/prefs';
import { unregisterPush } from '../../lib/push';
import { useSession } from '../../lib/session';
import { color } from '../../lib/theme';
import { RangeTabs } from '../../components/Charts';
import { Badge, Button, Card, Field, Icon, Loaded, PasswordField, Pairs, Problem, Screen, T } from '../../components/ui';

function ChangePassword() {
  const { signIn } = useSession();
  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [ok, setOk] = useState(false);
  const { busy, error, submit } = useSubmit(async () => { await signIn(await api.changePassword(cur, next)); setCur(''); setNext(''); setOk(true); });
  return (
    <View style={{ gap: 12 }}>
      <PasswordField label="Current password" value={cur} onChangeText={setCur} autoComplete="password" />
      <PasswordField label="New password" hint="At least 10 characters. Every other device is signed out." value={next}
        onChangeText={(t) => { setNext(t); setOk(false); }} autoComplete="password-new" />
      <Problem error={error} />
      {ok && <T tone="good" size={13}>Password changed. Other devices have been signed out.</T>}
      <Button label="Change password" busy={busy} disabled={!cur || next.length < 10} onPress={submit} />
    </View>
  );
}

function Close({ status, onDone }: { status: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [quote, setQuote] = useState<any>(null);
  const { busy, error, submit } = useSubmit(async () => { const r: any = await api.requestClosure(reason); setQuote(r.quote); onDone(); });
  if (status === 'closing' || quote) {
    return <T tone="text2">You have asked to close your account{quote ? `; your holding is worth ${money(quote.net_payable)} after fees today` : ''}.
      We pay it to your account on file, then close the account and delete your personal details. To change your mind, contact us before we pay.</T>;
  }
  if (!open) return <Button label="Close my account" onPress={() => setOpen(true)} />;
  return (
    <View style={{ gap: 12 }}>
      <T tone="text2">We will sell all your units at the price on the day we process it, pay the money to your account on file,
        and then delete your personal details.</T>
      <Field label="Anything you would like to tell us (optional)" value={reason} onChangeText={setReason} multiline />
      <Problem error={error} />
      <Button label="Yes, close my account" kind="danger" busy={busy} onPress={submit} />
      <Button label="Keep my account" kind="link" onPress={() => setOpen(false)} />
    </View>
  );
}

function Toggle({ label, hint, value, onChange }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingVertical: 4 }}>
      <View style={{ flex: 1, gap: 2 }}>
        <T size={15}>{label}</T>
        {hint ? <T tone="muted" size={12}>{hint}</T> : null}
      </View>
      <Switch value={value} onValueChange={onChange} trackColor={{ true: color.gold, false: color.line }} />
    </View>
  );
}

function Section({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <Card title={title} right={<Icon name={icon} size={18} tint={color.gold} />}>{children}</Card>
  );
}

/** Saves each change straight away; a failed save is shown and the switch goes back. */
function useSave() {
  const { update } = usePrefs();
  const [error, setError] = useState<Error | null>(null);
  const save = (c: Partial<Prefs>) => { setError(null); update(c).catch(setError); };
  return { save, error };
}

function Display() {
  const { prefs } = usePrefs();
  const { save, error } = useSave();
  return (
    <Section icon="eye" title="Display">
      <Toggle label="Hide my balances" hint="Amounts show as dots until you tap the eye at the top." value={prefs.hide_balances}
        onChange={(v) => save({ hide_balances: v })} />
      <Toggle label="Short numbers" hint="$12.5k instead of $12,480.00 in tiles and lists." value={prefs.compact_numbers}
        onChange={(v) => save({ compact_numbers: v })} />
      <View style={{ gap: 8 }}>
        <T size={15}>Chart opens on</T>
        <RangeTabs value={prefs.chart_range} onChange={(v) => save({ chart_range: v as Prefs['chart_range'] })} />
      </View>
      <T tone="muted" size={12}>Saved to your account, so the website shows the same.</T>
      <Problem error={error} />
    </Section>
  );
}

// [key, label, hint]: one row per kind of event, with a push and an email switch
const EVENTS: [keyof Prefs['push'], string, string][] = [
  ['live_trades', 'A trade opens', 'Market and side, the moment the fund opens a position.'],
  ['trades', 'A trade result', 'Profit or loss when a trade is published; your share is in the app.'],
  ['money', 'Money in', 'Your transfer noted and received.'],
  ['withdrawals', 'Withdrawals', 'Requested, approved, paid or declined.'],
  ['fees', 'Fees charged', 'Every two months, what came off your balance.'],
  ['statements', 'Statements ready', 'Your monthly PDF.'],
];

function Notifications() {
  const { prefs } = usePrefs();
  const { save, error } = useSave();
  return (
    <Section icon="bell" title="Notifications">
      <T tone="muted" size={12}>Everything also lands under the bell at the top. Password and payout-account changes
        are always sent, for your safety.</T>
      <View style={{ flexDirection: 'row', paddingTop: 4 }}>
        <T tone="muted" size={11} style={{ flex: 1 }}>TELL ME WHEN</T>
        <T tone="muted" size={11} style={{ width: 60, textAlign: 'center' }}>PUSH</T>
        <T tone="muted" size={11} style={{ width: 60, textAlign: 'center' }}>EMAIL</T>
      </View>
      {EVENTS.map(([k, label, hint]) => {
        const emailKey = k as keyof Prefs['email'];
        const emailSwitchable = k in prefs.email;
        return (
          <View key={k} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 6,
            borderTopColor: color.line, borderTopWidth: 1 }}>
            <View style={{ flex: 1, gap: 2, paddingRight: 8 }}>
              <T size={14}>{label}</T>
              <T tone="muted" size={11}>{hint}</T>
            </View>
            <View style={{ width: 60, alignItems: 'center' }}>
              <Switch value={!!prefs.push[k]} accessibilityLabel={`${label}: push`}
                onValueChange={(v) => save({ push: { ...prefs.push, [k]: v } })}
                trackColor={{ true: color.gold, false: color.line }} />
            </View>
            <View style={{ width: 60, alignItems: 'center' }}>
              {emailSwitchable ? (
                <Switch value={!!prefs.email[emailKey]} accessibilityLabel={`${label}: email`}
                  onValueChange={(v) => save({ email: { ...prefs.email, [emailKey]: v } })}
                  trackColor={{ true: color.gold, false: color.line }} />
              ) : <T tone="muted" size={11}>always</T>}
            </View>
          </View>
        );
      })}
      <Problem error={error} />
    </Section>
  );
}

const initials = (name: string) => String(name || '?').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');

export default function Account() {
  const me = useLoad(api.me);
  const { signOut, biometricAvailable, biometricOn, setBiometric } = useSession();
  return (
    <Screen onRefresh={me.reload}>
      <Loaded q={me}>
        {({ investor: i, terms: t }: any) => (
          <>
            <View style={{ flexDirection: 'row', gap: 14, alignItems: 'center', backgroundColor: color.surface, borderColor: color.line,
              borderWidth: 1, borderRadius: 20, padding: 16 }}>
              <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: color.gold, alignItems: 'center', justifyContent: 'center' }}>
                <T bold size={20} style={{ color: color.goldInk }}>{initials(i.name)}</T>
              </View>
              <View style={{ flex: 1, gap: 2 }}>
                <T bold size={18}>{i.name}</T>
                <T tone="muted" size={13}>{i.email}</T>
                <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                  <T tone="muted" size={12}>Member since {day(i.joined)}</T>
                  <Badge label={i.status} tone={i.status === 'active' ? 'good' : 'neutral'} />
                </View>
              </View>
            </View>
            <Section icon="user" title="Your details">
              <Pairs rows={[['Name', i.name], ['Email', i.email], ['Phone', i.phone || '—'], ['Country', i.country || '—']]} />
              <T tone="muted" size={12}>To change these, email us from the address above.</T>
            </Section>
            <Display />
            <Notifications />
            <Card title="Where we pay you" right={<Icon name="bank" size={18} tint={color.gold} />}>
              {i.payout.account_number ? (
                <Pairs rows={[['Bank', i.payout.bank_name], ['Account', `····${i.payout.account_number.slice(-4)}`], ['Name', i.payout.account_name]]} />
              ) : <T tone="text2">No account on file yet.</T>}
              <T tone="muted" size={13}>To protect you, this can only be changed by contacting us — never from the app.
                If someone got into your account, they still could not redirect your money.</T>
            </Card>
            {t && (
              <Section icon="doc" title="Your terms">
                <Pairs rows={[['Performance fee', `${t.performance_fee_pct}% of new profit`],
                  ['Management fee', `${t.management_fee_pct}% of each two-month period's profit`],
                  ['Minimum first deposit', money(t.min_investment)], ['Lock-up', `${t.lockup_days} days`],
                  ['Withdrawal notice', `${t.notice_days} days`]]} />
                <Button label="How it works" kind="link" onPress={() => router.push('/how')} />
              </Section>
            )}
            {biometricAvailable && (
              <Card title="Unlock" right={<Icon name="shield" size={18} tint={color.gold} />}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                  <T style={{ flex: 1 }}>Ask for fingerprint or face when the app opens</T>
                  <Switch value={biometricOn} onValueChange={setBiometric} trackColor={{ true: color.gold, false: color.line }} />
                </View>
              </Card>
            )}
            <Card title="Password"><ChangePassword /></Card>
            <Card title="Close your account"><Close status={i.status} onDone={me.reload} /></Card>
            <Button label="Sign out" onPress={async () => { await unregisterPush(); await signOut(); }} />
            <T tone="muted" size={12} style={{ textAlign: 'center' }}>Version {Application.nativeApplicationVersion ?? 'web'}
              {Application.nativeBuildVersion ? ` (${Application.nativeBuildVersion})` : ''}</T>
          </>
        )}
      </Loaded>
    </Screen>
  );
}
