import { Alert, Linking, Platform } from 'react-native';
import * as Application from 'expo-application';
import { latestRelease } from './api';

/**
 * A sideloaded app gets no store updates, so it checks for itself on launch:
 * if the published build's versionCode is higher than this one's, offer it.
 * The download opens in the browser, which hands the APK to Android's installer.
 */
export async function checkForUpdate(): Promise<void> {
  if (Platform.OS !== 'android') return;
  const release = await latestRelease();
  const mine = Number(Application.nativeBuildVersion || 0);
  if (!release || !mine || release.version_code <= mine) return;
  Alert.alert(
    `Update available (${release.version})`,
    (release.notes ? `${release.notes}\n\n` : '')
      + 'Download it and open the file to install. Your account and sign-in stay as they are.',
    [{ text: 'Later', style: 'cancel' }, { text: 'Download', onPress: () => Linking.openURL(release.url) }],
  );
}
