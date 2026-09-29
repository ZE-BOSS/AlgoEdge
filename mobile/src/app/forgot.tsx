import { useState } from 'react';
import { Text } from 'react-native';
import { api } from '../lib/api';
import { useSubmit } from '../lib/hooks';
import { color } from '../lib/theme';
import { Button, Card, Field, Problem, Screen } from '../components/ui';

export default function Forgot() {
  const [email, setEmail] = useState('');
  const [done, setDone] = useState<string | null>(null);
  const { busy, error, submit } = useSubmit(async () => { setDone((await api.forgot(email.trim())).message); });
  return (
    <Screen>
      <Card>
        {done ? <Text style={{ color: color.good }}>{done}</Text> : (
          <>
            <Text style={{ color: color.text2 }}>We will email a link to choose a new password. It opens in your
              browser; afterwards, sign in here with the new password.</Text>
            <Field label="Your email address" value={email} onChangeText={setEmail} autoCapitalize="none"
              keyboardType="email-address" autoComplete="email" />
            <Problem error={error} />
            <Button label="Email me a link" kind="primary" busy={busy} disabled={!email} onPress={submit} />
          </>
        )}
      </Card>
    </Screen>
  );
}
