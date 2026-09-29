import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { api } from '../lib/api';
import { useSubmit } from '../lib/hooks';
import { color } from '../lib/theme';
import { Button, Card, Field, Problem, Screen } from '../components/ui';

/** Open an account. The emailed link confirms the address and sets the password. */
export default function Signup() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [country, setCountry] = useState('');
  const [consent, setConsent] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const { busy, error, submit } = useSubmit(async () => {
    const r = await api.signup({ name: name.trim(), email: email.trim(), phone: phone.trim() || null,
                                 country: country.trim() || null, consent });
    setDone(r.message);
  });
  return (
    <Screen>
      <Card>
        {done ? (
          <View style={{ gap: 12 }}>
            <Text style={{ color: color.good }}>{done}</Text>
            <Text style={{ color: color.text2 }}>The link opens in your browser. Once you have chosen a
              password, come back and sign in here.</Text>
            <Button label="Back to sign in" kind="primary" onPress={() => router.back()} />
          </View>
        ) : (
          <>
            <Field label="Full name" value={name} onChangeText={setName} autoComplete="name" />
            <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" autoComplete="email"
              keyboardType="email-address" hint="We send a link here to confirm it and choose your password." />
            <Field label="Phone (optional)" value={phone} onChangeText={setPhone} keyboardType="phone-pad"
              autoComplete="tel" />
            <Field label="Country (optional)" value={country} onChangeText={setCountry} />
            <Pressable onPress={() => setConsent((v) => !v)} accessibilityRole="checkbox"
              accessibilityState={{ checked: consent }} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start' }}>
              <View style={{ width: 22, height: 22, borderRadius: 6, borderWidth: 2, marginTop: 2,
                borderColor: consent ? color.gold : color.muted, backgroundColor: consent ? color.gold : 'transparent',
                alignItems: 'center', justifyContent: 'center' }}>
                {consent ? <Text style={{ color: color.bg, fontWeight: '800', fontSize: 14 }}>✓</Text> : null}
              </View>
              <Text style={{ color: color.text2, flex: 1, fontSize: 14 }}>I understand that investing involves risk,
                that I can lose money, and that past performance does not guarantee future results.</Text>
            </Pressable>
            <Problem error={error} />
            <Button label="Create account" kind="primary" busy={busy}
              disabled={name.trim().length < 2 || !email || !consent} onPress={submit} />
          </>
        )}
      </Card>
    </Screen>
  );
}
