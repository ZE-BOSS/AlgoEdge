import HelpNote, { H } from './HelpNote';

/**
 * [Phase 14 Part F] Per-panel explanations for Fundamentals.
 *
 * Written against the three things that were actually reported as confusing —
 * "volume by price", depth's "empty book", and the GEX ticker format — rather
 * than a general glossary. Each note says what the number is, how it is
 * computed, what it cannot tell you, and one concrete way to act on it.
 */

export function OrderFlowHelp() {
  return (
    <HelpNote id="orderflow" title="What order flow shows, and how to read it">
      <p style={{ margin: '4px 0' }}>
        Every tick is classified as buyer- or seller-initiated by where it printed
        relative to the bid/ask, then aggregated.
      </p>
      <div><H.t>CVD</H.t> — cumulative delta: running total of (buy volume − sell
        volume). Rising = buyers lifting offers; falling = sellers hitting bids.</div>
      <div><H.t>Imbalance</H.t> — the same thing as a percentage of total volume,
        so it is comparable between symbols and windows.</div>
      <div><H.t>Volume by price</H.t> — the bubbles. Each row is one price
        <em> level</em>, not a moment in time: it answers “how much traded here,
        and which side was pushing”. Size is total volume at that price, colour is
        the sign of the delta.</div>
      <div><H.t>VPOC</H.t> — Volume Point of Control: the single price with the
        most traded volume in the window. The market’s centre of gravity.</div>
      <div><H.t>Value area</H.t> — the band containing ~70% of the window’s volume.
        Where the market agreed on price.</div>
      <H.use>
        Price returning to VPOC tends to find acceptance; the value-area edges tend
        to produce reaction. A CVD that rises while price does not (or the reverse)
        is a divergence — effort without result, which often precedes a turn.
      </H.use>
      <H.caveat>
        MT5 CFD ticks carry no aggressor flag and no exchange volume, so the buy/sell
        split is <em>inferred</em> from price against bid/ask, and “volume” is a tick
        count rather than traded size. Directionally useful; not a true tape read.
      </H.caveat>
    </HelpNote>
  );
}

export function DepthHelp() {
  return (
    <HelpNote id="depth" title="Why depth is often empty">
      <p style={{ margin: '4px 0' }}>
        Depth (level 2) is the book of resting limit orders above and below the
        current price — how much size is waiting, and where.
      </p>
      <div>
        <H.t>“Empty book”</H.t> is not an error. It means the broker returned no
        depth for that symbol. Most CFD brokers, including Deriv, do not publish a
        real order book: you are trading against the broker, not a central exchange,
        so there is no shared book to publish.
      </div>
      <div style={{ marginTop: 6 }}>
        <H.t>“market_book_add failed”</H.t> means the terminal refused to subscribe
        at all — the same limitation, surfaced one step earlier.
      </div>
      <H.use>
        Where depth <em>is</em> available, a large resting size just beyond price is
        a level that must be absorbed before price can pass — useful for placing
        targets just in front of it rather than beyond it.
      </H.use>
      <H.caveat>
        Expect this panel to stay empty on Deriv CFDs. It is wired for venues that
        do publish depth; it is not broken.
      </H.caveat>
    </HelpNote>
  );
}

export function CorrelationHelp() {
  return (
    <HelpNote id="correlation" title="What the correlation matrix is for">
      <p style={{ margin: '4px 0' }}>
        Rolling correlation of returns between the symbols you list, computed from
        your own MT5 bars. +1 means they move together, −1 exactly opposite, 0 means
        unrelated.
      </p>
      <H.use>
        Two positions on symbols correlated at +0.8 are close to one position at
        double size — the risk is not diversified. Use it to avoid stacking the same
        bet, and to pick portfolio legs whose drawdowns do not overlap.
      </H.use>
      <H.caveat>
        Correlation is not stable. A pair that is uncorrelated in calm conditions
        often goes to +1 in a shock, which is exactly when the diversification was
        supposed to help.
      </H.caveat>
    </HelpNote>
  );
}

export function GexHelp() {
  return (
    <HelpNote id="gex" title="Gamma exposure — and why the ticker matters">
      <p style={{ margin: '4px 0' }}>
        GEX estimates how much dealers must hedge as price moves, aggregated from
        the options chain by strike.
      </p>
      <div>
        <H.t>Positive GEX</H.t> — dealers are long gamma and hedge <em>against</em>
        the move (selling rallies, buying dips). Tends to suppress volatility.
      </div>
      <div>
        <H.t>Negative GEX</H.t> — dealers are short gamma and hedge <em>with</em> the
        move. Amplifies volatility; moves extend rather than fade.
      </div>
      <div>
        <H.t>Gamma flip</H.t> — the level where the sign changes. Often behaves as a
        regime boundary rather than a support/resistance line.
      </div>
      <div style={{
        marginTop: 8, padding: '6px 8px', borderRadius: 4,
        background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.3)',
      }}>
        <b>Use the index code, not your broker’s symbol.</b> Options are listed on the
        index itself, so this field wants <code>SPX</code>, <code>NDX</code>,
        <code> RUT</code>, <code>DJI</code> — or a single-name ticker like{' '}
        <code>AAPL</code>. Broker CFD names such as <code>SPX500</code>,{' '}
        <code>NDX100</code> or <code>US Tech 100</code> have no options chain and
        will return nothing.
      </div>
      <H.use>
        In positive GEX, mean-reversion setups toward VWAP or the flip level work
        better. In negative GEX, breakout and continuation setups do — and stops
        need more room, because moves extend.
      </H.use>
      <H.caveat>
        Free chains are delayed (~15 minutes) and dealer positioning is inferred from
        open interest, not disclosed. Treat GEX as a regime signal, not a precise level.
      </H.caveat>
    </HelpNote>
  );
}

export function CalendarHelp() {
  return (
    <HelpNote id="calendar" title="Economic calendar">
      <p style={{ margin: '4px 0' }}>
        Scheduled releases with an expected impact rating, from ForexFactory’s
        weekly feed — the same source the live bot’s news filter uses.
      </p>
      <H.use>
        High-impact releases (NFP, CPI, central-bank decisions) widen spreads and
        produce gaps that jump stops. The usual handling is to avoid entering in a
        blackout window either side, rather than to trade the release.
      </H.use>
      <H.caveat>
        Scheduled events only. It cannot warn you about unscheduled ones, which are
        the moves most likely to hurt.
      </H.caveat>
    </HelpNote>
  );
}

export const FUNDAMENTALS_HELP = {
  orderflow: OrderFlowHelp,
  orderbook: DepthHelp,
  correlation: CorrelationHelp,
  gex: GexHelp,
  calendar: CalendarHelp,
};
