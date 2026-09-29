import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useLocalSearchParams } from 'expo-router';
import { api } from '../../lib/api';
import { cleanAmount, day, money } from '../../lib/format';
import { useLoad, useSubmit } from '../../lib/hooks';
import { color } from '../../lib/theme';
import { Button, Card, Field, Loaded, Pairs, Problem, Screen, Segmented, T } from '../../components/ui';

function CopyValue({ value }: { value: string }) {
  const [done, setDone] = useState(false);
  return (
    <Pressable onPress={() => Clipboard.setStringAsync(value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}
      hitSlop={8} accessibilityRole="button" accessibilityLabel={`Copy ${value}`}>
      <Text style={{ color: color.text, fontFamily: 'monospace', fontSize: 15 }}>{value} <Text style={{ color: color.gold, fontSize: 13 }}>{done ? 'copied' : 'copy'}</Text></Text>
    </Pressable>
  );
}

function AddMoney() {
  const info = useLoad(api.instructions);
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [done, setDone] = useState<any>(null);
  const clean = cleanAmount(amount);
  const { busy, error, submit } = useSubmit(async () => { setDone(await api.claimDeposit(clean!, note)); setAmount(''); setNote(''); });
  return (
    <Loaded q={info}>
      {(i: any) => (
        <>
          <Card title="1 · Send a bank transfer">
            {!i.configured ? <T tone="text2">Our bank details are not set up here yet. Contact us and we will send them.</T> : (
              <Pairs rows={[['Bank', i.bank_name], ['Account number', <CopyValue key="n" value={i.account_number} />],
                ['Account name', i.account_name], ['Reference', <CopyValue key="r" value={i.reference_code} />]]} />
            )}
            <T tone="text2" size={13}>Always put {i.reference_code} as the reference — it is how we match the transfer to you. {i.instructions || ''}</T>
          </Card>
          <Card title="2 · Tell us you have sent it">
            {done ? (
              <View style={{ gap: 8 }}>
                <T tone="good" bold>Thank you — we are looking out for {money(done.amount_claimed)}.</T>
                <T tone="text2">When it arrives we confirm what actually landed and buy your units at that day’s price.</T>
                <Button label="Tell us about another transfer" kind="link" onPress={() => setDone(null)} />
              </View>
            ) : (
              <>
                <Field label={`Amount sent (${i.currency})`} hint={`Minimum ${money(i.min_investment)}.`} value={amount}
                  onChangeText={setAmount} keyboardType="decimal-pad" placeholder="1,000" />
                <Field label="Anything we should know (optional)" value={note} onChangeText={setNote} maxLength={500} />
                <Problem error={error} />
                <Button label="I have sent it" kind="primary" busy={busy} disabled={!clean} onPress={submit} />
                <T tone="muted" size={13}>Nothing is added to your account until we see the money arrive.</T>
              </>
            )}
          </Card>
        </>
      )}
    </Loaded>
  );
}

function Withdraw() {
  const me = useLoad(api.me);
  const [amount, setAmount] = useState('');
  const [debounced, setDebounced] = useState('');
  const [reason, setReason] = useState('');
  const [done, setDone] = useState<any>(null);
  const clean = cleanAmount(amount);
  useEffect(() => { const t = setTimeout(() => setDebounced(clean || ''), 300); return () => clearTimeout(t); }, [clean]);
  const quote = useLoad(() => api.quote(debounced || undefined), debounced);
  const q: any = quote.data;
  const live = !!(q && clean && clean === debounced && Number(q.amount) === Number(clean));
  const needsReason = live && q.needs_reason;
  const tooMuch = live && q.exceeds_holding;
  const { busy, error, submit } = useSubmit(async () => {
    if (needsReason && !reason.trim()) throw new Error('Tell us what the money is for.');
    setDone(await api.withdraw(clean!, needsReason ? reason : undefined));
    setAmount(''); setReason(''); quote.reload();
  });
  return (
    <Loaded q={me}>
      {({ investor, terms }: any) => (
        <>
          <Card title="What you can take out">
            <Loaded q={quote}>
              {(qq: any) => (
                <>
                  <Pairs rows={[['Your holding today', money(qq.holding_value)], ['Standard limit this month', money(qq.standard_limit)]]} />
                  <T tone="text2" size={13}>
                    The standard limit is {qq.cap_pct}% of what your holding earned this month ({money(qq.month_profit)}).
                    {qq.explanation.startsWith('no profit') ? ' Nothing has been earned yet this month, so any withdrawal is reviewed by us first.' : ''}
                    {qq.in_lockup ? ` Your money is in its lock-up period until ${day(qq.lockup_until)}, so a withdrawal before then is reviewed first.` : ''}
                    {qq.cooling_off ? ' The account we pay you was changed in the last 48 hours, so every withdrawal is reviewed first.' : ''}
                  </T>
                </>
              )}
            </Loaded>
          </Card>
          <Card title="Request a withdrawal">
            {investor.status === 'closing' ? <T tone="text2">Your account is being closed. The closing payment covers everything.</T>
              : !investor.payout.account_number ? <T tone="text2">We do not have a bank account on file to pay you. Contact us to add one — for your protection it cannot be changed from the app.</T>
              : done ? (
                <View style={{ gap: 8 }}>
                  <T tone="good" bold>Request received for {money(done.amount_requested)}.</T>
                  <T tone="text2">{done.is_exception ? 'It is above the standard limit, so we review it first and will be in touch.' : `We will pay it within ${terms.notice_days} days.`}</T>
                  <Button label="Make another request" kind="link" onPress={() => setDone(null)} />
                </View>
              ) : (
                <>
                  <Field label="Amount (USD)" value={amount} onChangeText={setAmount} keyboardType="decimal-pad" placeholder="0.00" />
                  {tooMuch && <T tone="bad" size={13}>That is more than your holding is worth today.</T>}
                  {needsReason && !tooMuch && (
                    <Field label="What is it for?" hint="This amount needs our review first. A short reason helps us decide quickly."
                      value={reason} onChangeText={setReason} multiline maxLength={2000} />
                  )}
                  <T tone="muted" size={13}>Paid to {investor.payout.bank_name} ····{investor.payout.account_number.slice(-4)} ({investor.payout.account_name}).
                    Standard requests are paid within {terms.notice_days} days.</T>
                  <Problem error={error} />
                  <Button label={needsReason ? 'Send for review' : 'Request withdrawal'} kind="primary" busy={busy}
                    disabled={!clean || tooMuch} onPress={submit} />
                </>
              )}
          </Card>
        </>
      )}
    </Loaded>
  );
}

export default function Money() {
  // the sub-tab lives in the route, so "Withdraw" on the overview lands here directly
  const params = useLocalSearchParams<{ tab?: string }>();
  const tab = params.tab === 'withdraw' ? 'withdraw' : 'add';
  return (
    <Screen>
      <Segmented value={tab} onChange={(k) => router.setParams({ tab: k })}
        options={[['add', 'Add money'], ['withdraw', 'Withdraw']]} />
      {tab === 'add' ? <AddMoney /> : <Withdraw />}
    </Screen>
  );
}
