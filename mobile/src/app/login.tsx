import { useState } from 'react';
import { Image, KeyboardAvoidingView, Platform, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { api } from '../lib/api';
import { useSubmit } from '../lib/hooks';
import { useSession } from '../lib/session';
import { color } from '../lib/theme';
import { Button, Field, Problem } from '../components/ui';

export default function Login() {
  const { signIn } = useSession();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const { busy, error, submit } = useSubmit(async () => { await signIn(await api.login(email.trim(), password)); });
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: color.bg }}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={st.wrap}>
        <View style={{ alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <Image source={require('../../assets/splash-icon.png')} style={{ width: 72, height: 72 }} />
          <Text style={st.brand}>ALPHAVANTIQ <Text style={{ color: color.gold, fontWeight: '500' }}>CAPITAL</Text></Text>
          <Text style={{ color: color.text2 }}>Sign in to your investor account</Text>
        </View>
        <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" autoComplete="email"
          keyboardType="email-address" textContentType="username" />
        <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry autoComplete="password"
          textContentType="password" onSubmitEditing={submit} />
        <Problem error={error} />
        <Button label="Sign in" kind="primary" busy={busy} disabled={!email || !password} onPress={submit} />
        <View style={{ alignItems: 'center', gap: 10, marginTop: 6 }}>
          <Button label="Forgotten your password?" kind="link" onPress={() => router.push('/forgot')} />
          <Text style={{ color: color.muted, fontSize: 13, textAlign: 'center' }}>
            No password yet? Open the invitation link we emailed you, then sign in here.
          </Text>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const st = StyleSheet.create({
  wrap: { flex: 1, justifyContent: 'center', padding: 24, gap: 14 },
  brand: { color: color.text, fontSize: 15, fontWeight: '700', letterSpacing: 3 },
});
