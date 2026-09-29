import { useState } from 'react';
import { Switch, View } from 'react-native';
import * as Application from 'expo-application';
import { api } from '../../lib/api';
import { day, money } from '../../lib/format';
import { useLoad, useSubmit } from '../../lib/hooks';
import { unregisterPush } from '../../lib/push';
import { useSession } from '../../lib/session';
import { color } from '../../lib/theme';
import { Button, Card, Field, Loaded, Pairs, Problem, Screen, T } from '../../components/ui';

function ChangePassword() {
  const { signIn } = useSession();
  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [ok, setOk] = useState(false);
  const { busy, error, submit } = useSubmit(async () => { await signIn(await api.changePassword(cur, next)); setCur(''); setNext(''); setOk(true); });
  return (
    <View style={{ gap: 12 }}>
      <Field label="Current password" value={cur} onChangeText={setCur} secureTextEntry autoComplete="password" />
      <Field label="New password" hint="At least 10 characters. Every other device is signed out." value={next}
        onChangeText={(t) => { setNext(t); setOk(false); }} secureTextEntry autoComplete="password-new" />
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

export default function Account() {
  const me = useLoad(api.me);
  const { signOut, biometricAvailable, biometricOn, setBiometric } = useSession();
  return (
    <Screen onRefresh={me.reload}>
      <Loaded q={me}>
        {({ investor: i }: any) => (
          <>
            <Card title="Your details">
              <Pairs rows={[['Name', i.name], ['Email', i.email], ['Phone', i.phone || '—'], ['Member since', day(i.joined)]]} />
            </Card>
            <Card title="Where we pay you">
              {i.payout.account_number ? (
                <Pairs rows={[['Bank', i.payout.bank_name], ['Account', `····${i.payout.account_number.slice(-4)}`], ['Name', i.payout.account_name]]} />
              ) : <T tone="text2">No account on file yet.</T>}
              <T tone="muted" size={13}>To protect you, this can only be changed by contacting us — never from the app.
                If someone got into your account, they still could not redirect your money.</T>
            </Card>
            {biometricAvailable && (
              <Card title="Unlock">
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
