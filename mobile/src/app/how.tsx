import { View } from 'react-native';
import { api } from '../lib/api';
import { money, units } from '../lib/format';
import { useLoad } from '../lib/hooks';
import { color } from '../lib/theme';
import { Card, Loaded, Pairs, Screen, T } from '../components/ui';

function Sum({ parts }: { parts: [string, string][] }) {
  return (
    <View style={{ gap: 6 }}>
      {parts.map(([k, v], i) => (
        <View key={k} style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4,
          borderTopColor: i === parts.length - 1 ? color.line : 'transparent', borderTopWidth: 1 }}>
          <T tone={i === parts.length - 1 ? undefined : 'text2'} bold={i === parts.length - 1}>{k}</T>
          <T mono bold={i === parts.length - 1}>{v}</T>
        </View>
      ))}
    </View>
  );
}

export default function How() {
  const me = useLoad(api.me);
  return (
    <Screen>
      <Loaded q={me}>
        {({ statement: s, terms }: any) => (
          <>
            <Card title="Your value">
              <T tone="text2">The fund is divided into units. Money in buys units at that day’s price; a withdrawal sells them.
                The price moves with the fund’s results, so everyone’s holding moves by the same percentage.</T>
              <T tone="text2">When a closed trade is published, its result moves the price straight away, so your balance
                changes by exactly your share of it, the amount the Trades tab shows. A loss comes off any profit first,
                then off your capital.</T>
              <Sum parts={[['Units you hold', units(s.units)], ['× unit price', money(s.nav_per_unit)], ['= your holding', money(s.current_value)]]} />
            </Card>
            <Card title="Your profit">
              <Sum parts={[['Holding', money(s.current_value)], ['+ paid out to you', money(s.withdrawn)],
                ['− paid in', money(s.capital_in)], ['= profit', money(s.profit, { sign: true })]]} />
            </Card>
            <Card title="Your terms">
              <Pairs rows={[['Performance fee', `${terms.performance_fee_pct}% of new profit`],
                ['Management fee', `${terms.management_fee_pct}% of each two-month period's profit`],
                ['Standard withdrawal limit', `${terms.withdrawal_cap_pct}% of monthly profit`],
                ['Lock-up', `${terms.lockup_days} days`], ['Notice', `${terms.notice_days} days`]]} />
              <T tone="muted" size={13}>These are the terms in force when your money went in (version {terms.version}). The
                performance fee is charged only on profit above your previous high point.</T>
            </Card>
          </>
        )}
      </Loaded>
    </Screen>
  );
}
