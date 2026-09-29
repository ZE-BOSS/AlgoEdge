import { useEffect, useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import { useSession } from '../lib/session';
import { color } from '../lib/theme';
import { Button } from './ui';

/** Shown on open, and after five minutes away, when unlock is turned on. */
export default function LockScreen() {
  const { unlock, signOut } = useSession();
  const [failed, setFailed] = useState(false);
  useEffect(() => { unlock().then((ok) => setFailed(!ok)); }, [unlock]);
  return (
    <View style={st.wrap}>
      <Image source={require('../../assets/splash-icon.png')} style={{ width: 96, height: 96 }} />
      <Text style={st.title}>ALPHAVANTIQ <Text style={{ color: color.gold, fontWeight: '500' }}>CAPITAL</Text></Text>
      <Text style={{ color: color.text2, textAlign: 'center' }}>
        {failed ? 'Unlock to see your account.' : 'Unlocking…'}
      </Text>
      <View style={{ alignSelf: 'stretch', gap: 12 }}>
        <Button label="Unlock" kind="primary" onPress={() => unlock().then((ok) => setFailed(!ok))} />
        <Button label="Sign out instead" kind="link" onPress={signOut} />
      </View>
    </View>
  );
}

const st = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: color.bg, alignItems: 'center', justifyContent: 'center', padding: 32, gap: 18 },
  title: { color: color.text, fontSize: 15, fontWeight: '700', letterSpacing: 3 },
});
