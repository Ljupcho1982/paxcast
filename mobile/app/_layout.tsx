import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';
import {
  useFonts,
  Archivo_400Regular,
  Archivo_600SemiBold,
  Archivo_800ExtraBold,
} from '@expo-google-fonts/archivo';
import { Colors } from '@/constants/theme';
import { FirstRunNotice } from '@/components/FirstRunNotice';

SplashScreen.preventAutoHideAsync().catch(() => {});

export default function RootLayout() {
  // Archivo at 400/600/800 is load-bearing for the Modernist system: the
  // 800 weight carries every heading and numeric readout, and falling back to
  // a system sans changes the visual weight of the whole design. Hold the
  // splash until the faces are resident rather than flashing a substitute.
  const [loaded, error] = useFonts({
    Archivo_400Regular,
    Archivo_600SemiBold,
    Archivo_800ExtraBold,
  });

  useEffect(() => {
    if (loaded || error) SplashScreen.hideAsync().catch(() => {});
  }, [loaded, error]);

  if (!loaded && !error) return null;

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: Colors.bg },
          headerTintColor: Colors.text,
          headerTitleStyle: { fontWeight: '600' },
          contentStyle: { backgroundColor: Colors.bg },
          headerShadowVisible: false,
        }}
      >
        <Stack.Screen name="index" options={{ title: 'PaxCast' }} />
        <Stack.Screen name="airport/[iata]" options={{ title: 'Forecast' }} />
        <Stack.Screen name="scenario/[iata]" options={{ title: 'Scenario' }} />
        {/* Modernist checkpoint screens: headerShown false, because the design
            supplies its own app bar with the accent mark and date meta. */}
        <Stack.Screen name="checkpoint/index" options={{ headerShown: false }} />
        <Stack.Screen name="checkpoint/[cp]" options={{ headerShown: false }} />
        <Stack.Screen name="contribute/airport" options={{ title: 'Add airport' }} />
        <Stack.Screen name="contribute/schedule/[iata]" options={{ title: 'Import schedule' }} />
        <Stack.Screen name="contribute/report/[iata]" options={{ title: 'Report a wait' }} />
      </Stack>
      {/* Rendered last so it sits above the navigator: the limitations are not
          advice you scroll past, and the first screen shows forecast numbers. */}
      <FirstRunNotice />
    </SafeAreaProvider>
  );
}
