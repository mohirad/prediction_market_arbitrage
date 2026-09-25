"""Renders the ranked-opportunities dashboard as one self-contained HTML
file (no server, no external assets -- open it straight from the
filesystem). Phase 5 in the project brief is meant to come last, after
matching/pricing/convergence-study/trade-construction exist; this is a
deliberately small slice of it (a table + per-leg instructions) built on
top of the real Phase 0 clients and the Phase 2 hard-arb engine that
already exist, not a mockup with placeholder numbers.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction

from arb.matching.store import MatchedPair
from arb.money import Cents, PriceBp
from arb.pricing.hard_arb import ArbEvaluation


@dataclass(frozen=True)
class PairResult:
    pair: MatchedPair
    generated_at: datetime
    best_arb: ArbEvaluation | None       # None if no data could be fetched
    soft_divergence_bps: int | None      # signed mid-price gap, common-YES frame
    error: str | None = None


def _cents_to_display(c: Cents | None) -> str:
    if c is None:
        return "-"
    sign = "-" if c < 0 else ""
    return f"{sign}${abs(c) / 100:.2f}"


def _price_to_display(p: PriceBp | None) -> str:
    if p is None:
        return "-"
    return f"{int(p) / 100:.2f}c"


def _fmt_size(size: Fraction | int) -> str:
    f = Fraction(size)
    if f.denominator == 1:
        return str(f.numerator)
    return f"{float(f.numerator) / f.denominator:.2f}"


def _leg_instruction_html(leg, platform_url: str) -> str:
    action = "BUY YES" if leg.side == "yes" else "BUY NO"
    warn = "" if leg.fully_fillable else (
        f'<div class="warn">Only {_fmt_size(leg.filled_contracts)} of {leg.contracts} '
        f"contracts available at this price -- book doesn't have full depth.</div>"
    )
    return f"""
    <div class="leg">
      <div class="leg-platform">{html.escape(leg.platform.title())}</div>
      <div class="leg-action">{action} <span class="leg-qty">x{leg.contracts}</span></div>
      <div class="leg-detail">market: <code>{html.escape(leg.market_id)}</code></div>
      <div class="leg-detail">avg price: {_price_to_display(leg.avg_price)}
        &middot; cost: {_cents_to_display(leg.cost_cents)}
        &middot; est. fee: {_cents_to_display(leg.fee_cents)}</div>
      <a class="leg-link" href="{html.escape(platform_url)}" target="_blank" rel="noopener">open market &rarr;</a>
      {warn}
    </div>
    """


def _pair_card_html(result: PairResult) -> str:
    pair = result.pair
    notes = html.escape((pair.notes or "").strip())
    confidence_pct = f"{pair.confidence * 100:.0f}%"

    if result.error:
        return f"""
        <div class="card card-error">
          <div class="card-title">{html.escape(pair.pair_id)}</div>
          <div class="error-msg">Could not fetch live data: {html.escape(result.error)}</div>
        </div>
        """

    arb = result.best_arb
    is_hard_arb = arb is not None and arb.net_edge_cents > 0

    if is_hard_arb:
        badge = '<span class="badge badge-hard">HARD ARB &mdash; locked profit</span>'
        edge_line = (
            f'<div class="edge edge-positive">net edge: {_cents_to_display(arb.net_edge_cents)} '
            f"on {arb.size} contracts ({arb.net_edge_bps} bps)</div>"
        )
        legs_html = (
            _leg_instruction_html(arb.cheap_leg, pair.kalshi_url() if arb.cheap_leg.platform == "kalshi" else pair.polymarket_url())
            + _leg_instruction_html(arb.expensive_leg, pair.kalshi_url() if arb.expensive_leg.platform == "kalshi" else pair.polymarket_url())
        )
        guide = f"""
        <div class="guide">
          <strong>To complete this arbitrage:</strong>
          <ol>
            <li>Open both market links below in separate tabs.</li>
            <li>Place the BUY order shown on each platform, at or better than the listed avg price.
                Prices move -- if either fills much worse than shown, re-check the numbers before
                completing the second leg.</li>
            <li>Once both legs fill, hold to settlement. Exactly one side pays out $1/contract
                regardless of the outcome -- that payout minus what you paid (both costs + fees,
                shown above) is your locked profit.</li>
          </ol>
        </div>
        """
    else:
        badge = '<span class="badge badge-soft">no locked arb right now</span>'
        gap = result.soft_divergence_bps
        gap_txt = "-" if gap is None else f"{gap/100:.2f}c"
        edge_line = f'<div class="edge">mid-price gap: {gap_txt} (directional only, not risk-free)</div>'
        legs_html = ""
        guide = """
        <div class="guide guide-muted">
          No combination of legs clears fees and slippage right now, so there's nothing
          risk-free to execute. A directional bet on the gap closing is possible but is
          NOT covered by this dashboard yet (that's Phase 3/4 -- convergence-probability
          modeling and exit rules aren't built).
        </div>
        """

    return f"""
    <div class="card {'card-hard' if is_hard_arb else 'card-soft'}">
      <div class="card-header">
        <div class="card-title">{html.escape(pair.pair_id)}</div>
        {badge}
      </div>
      <div class="card-meta">category: {html.escape(pair.category or '-')}
        &middot; match confidence: {confidence_pct}
        &middot; data as of {result.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
      {edge_line}
      <div class="legs">{legs_html}</div>
      {guide}
      {f'<div class="notes">{notes}</div>' if notes else ''}
    </div>
    """


def render_dashboard(results: list[PairResult]) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ranked = sorted(
        results,
        key=lambda r: (r.best_arb.net_edge_cents if (r.best_arb and r.best_arb.net_edge_cents > 0) else -1),
        reverse=True,
    )
    cards = "\n".join(_pair_card_html(r) for r in ranked)
    n_hard = sum(1 for r in results if r.best_arb and r.best_arb.net_edge_cents > 0)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi &lt;-&gt; Polymarket divergence scanner</title>
<style>
  :root {{
    --bg: #0b0d12; --panel: #12151c; --border: #232733; --text: #e7e9ee; --muted: #8b91a1;
    --green: #34d399; --red: #f87171; --accent: #60a5fa;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px 16px 64px; }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 24px; }}
  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px; margin-bottom: 16px; }}
  .card-hard {{ border-color: var(--green); }}
  .card-error {{ border-color: var(--red); }}
  .card-header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
  .card-title {{ font-weight: 600; font-size: 15px; }}
  .card-meta {{ color: var(--muted); font-size: 12px; margin: 6px 0 10px; }}
  .badge {{ font-size: 11px; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }}
  .badge-hard {{ background: rgba(52,211,153,.15); color: var(--green); border: 1px solid rgba(52,211,153,.4); }}
  .badge-soft {{ background: rgba(139,145,161,.15); color: var(--muted); border: 1px solid var(--border); }}
  .edge {{ font-size: 14px; margin: 8px 0; color: var(--muted); }}
  .edge-positive {{ color: var(--green); font-weight: 600; }}
  .legs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 12px 0; }}
  @media (max-width: 600px) {{ .legs {{ grid-template-columns: 1fr; }} }}
  .leg {{ background: #0e1117; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }}
  .leg-platform {{ font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
  .leg-action {{ font-weight: 700; margin: 2px 0; }}
  .leg-qty {{ color: var(--accent); font-weight: 500; }}
  .leg-detail {{ font-size: 12.5px; color: var(--muted); margin-top: 2px; }}
  .leg-link {{ display: inline-block; margin-top: 8px; font-size: 12.5px; color: var(--accent); text-decoration: none; }}
  .leg-link:hover {{ text-decoration: underline; }}
  code {{ background: #0e1117; padding: 1px 4px; border-radius: 4px; font-size: 12px; }}
  .warn {{ color: #fbbf24; font-size: 12px; margin-top: 6px; }}
  .guide {{ font-size: 13px; color: var(--text); background: #0e1117; border-radius: 8px; padding: 10px 14px; margin-top: 10px; }}
  .guide ol {{ margin: 6px 0 0; padding-left: 20px; }}
  .guide-muted {{ color: var(--muted); }}
  .error-msg {{ color: var(--red); font-size: 13px; }}
  .notes {{ font-size: 12px; color: var(--muted); margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; }}
  .footer {{ color: var(--muted); font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Kalshi &lt;-&gt; Polymarket divergence scanner</h1>
  <div class="sub">{len(results)} matched pair(s) evaluated &middot; {n_hard} with a locked-profit arb right now
    &middot; generated {generated_at} &middot; only human-approved pairs are shown</div>
  {cards if cards else '<div class="card">No approved matched pairs in data/matches.yaml yet.</div>'}
  <div class="footer">
    Every number above comes from a live order-book walk at the moment this page was generated,
    net of the fee model in <code>arb/fees.py</code> (see <code>docs/api_notes.md</code> for what's
    still unverified there -- notably Kalshi's exact fee multiplier). Prices move; re-generate before
    trusting a number that's more than a minute or two old. This page does not place orders.
  </div>
</div>
</body>
</html>"""
