"""Portfolio Trade Order Compliance & Execution Checker.

Run with no arguments for an interactive menu:
    python main.py

Or run non-interactively (original CLI behaviour):
    python main.py --no-menu --portfolios portfolios.json --trades trades_test.csv
"""

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PORTFOLIOS = BASE_DIR / "portfolios.json"
DEFAULT_TRADES = BASE_DIR / "trades_test.csv"

VALID_ACTIONS = ("Buy", "Sell")
VALID_SECURITY_TYPES = ("Equity", "Bond", "ETF")


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------
def decimal_value(value):
    """Safely convert any input to a finite Decimal, else return None."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def evaluate_trade(trade, portfolio):
    """Run the ordered compliance rule chain and return (decision, reason)."""
    quantity = decimal_value(trade.get("quantity"))
    price = decimal_value(trade.get("price"))
    action = str(trade.get("action", "")).strip()

    # Step 1 - Validate
    if quantity is None or price is None or quantity <= 0 or price <= 0:
        return "Rejected", "Invalid trade data."
    if action not in {"Buy", "Sell"}:
        return "Rejected", "Invalid trade data."

    # Step 2 - Restricted security check
    security_id = str(trade.get("security_id", "")).strip()
    if security_id in set(portfolio.get("restricted_securities", []) or []):
        return "Rejected", "Security is restricted for this portfolio."

    holdings = portfolio.get("current_holdings", []) or []
    holdings_by_security = {
        str(holding["security_id"]).strip(): holding
        for holding in holdings
    }

    # Step 3 - Sell check
    if action == "Sell":
        holding = holdings_by_security.get(security_id)
        existing_quantity = (
            decimal_value(holding["quantity"]) if holding is not None else Decimal("0")
        )
        if existing_quantity is None or existing_quantity < quantity:
            return "Rejected", "Insufficient holdings to sell."
        return "Approved", ""

    # Step 4 - Buy affordability check
    cash_balance = decimal_value(portfolio.get("cash_balance"))
    if cash_balance is None:
        raise ValueError("Portfolio cash_balance is missing or invalid.")

    trade_cost = quantity * price
    if trade_cost > cash_balance:
        return "Rejected", "Insufficient cash for this buy order."

    # Step 5 - Position concentration check (Buy only)
    max_position_pct = decimal_value(portfolio.get("max_position_pct"))
    if max_position_pct is None:
        raise ValueError("Portfolio max_position_pct is missing or invalid.")

    existing_holding = holdings_by_security.get(security_id)
    existing_quantity = (
        decimal_value(existing_holding["quantity"])
        if existing_holding is not None
        else Decimal("0")
    )
    if existing_quantity is None:
        raise ValueError("Holding quantity is invalid.")

    post_trade_cash = cash_balance - trade_cost
    post_trade_quantity = existing_quantity + quantity
    post_trade_security_value = post_trade_quantity * price

    value_of_other_holdings = Decimal("0")
    for holding in holdings:
        if str(holding["security_id"]).strip() != security_id:
            holding_quantity = decimal_value(holding["quantity"])
            market_price = decimal_value(holding["market_price"])
            if holding_quantity is None or market_price is None:
                raise ValueError("Holding quantity or market_price is invalid.")
            value_of_other_holdings += holding_quantity * market_price

    total_portfolio_value = (
        post_trade_cash + post_trade_security_value + value_of_other_holdings
    )
    position_pct = (
        post_trade_security_value / total_portfolio_value
        if total_portfolio_value != 0
        else Decimal("0")
    )

    if position_pct > max_position_pct:
        return (
            "Needs Manual Sign-off",
            "Exceeds maximum position concentration - requires approval.",
        )

    # Step 6 - Approved
    return "Approved", ""


def decide(trade, portfolios):
    """Resolve the portfolio then evaluate; never raises for bad trade data."""
    portfolio_id = str(trade.get("portfolio_id", "")).strip()
    if portfolio_id not in portfolios:
        return "Rejected", "Portfolio not found."
    return evaluate_trade(trade, portfolios[portfolio_id])


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_portfolios(path):
    """Load portfolio reference data, indexed by portfolio_id for O(1) lookup."""
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict) and "portfolios" in data:
        data = data["portfolios"]

    if isinstance(data, list):
        return {
            str(portfolio["portfolio_id"]).strip(): portfolio
            for portfolio in data
            if isinstance(portfolio, dict) and "portfolio_id" in portfolio
        }
    if isinstance(data, dict):
        return data
    raise ValueError("Portfolio JSON must contain an object or a list.")


def load_trades(path):
    """Load proposed trades from CSV as a list of plain dictionaries."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        trades = list(csv.DictReader(file))
    # Normalise to plain JSON-compatible dicts (mirrors an API payload shape).
    return json.loads(json.dumps(trades))


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------
DECISION_ICON = {
    "Approved": "[OK]  ",
    "Rejected": "[X]   ",
    "Needs Manual Sign-off": "[?]   ",
}


def print_results_table(rows):
    """Print decisions as an aligned, readable table."""
    header = f"{'#':<4}{'PORTFOLIO':<12}{'SECURITY':<12}{'ACTION':<8}{'QTY':>8}{'PRICE':>12}  {'DECISION':<24}REASON"
    print()
    print(header)
    print("-" * len(header))

    for index, (trade, decision, reason) in enumerate(rows, start=1):
        print(
            f"{index:<4}"
            f"{str(trade.get('portfolio_id', '')):<12}"
            f"{str(trade.get('security_id', '')):<12}"
            f"{str(trade.get('action', '')):<8}"
            f"{str(trade.get('quantity', '')):>8}"
            f"{str(trade.get('price', '')):>12}  "
            f"{decision:<24}"
            f"{reason}"
        )
    print("-" * len(header))
    print_summary(rows)


def print_summary(rows):
    """Print an aggregate count per decision type."""
    counts = {"Approved": 0, "Rejected": 0, "Needs Manual Sign-off": 0}
    for _, decision, _ in rows:
        counts[decision] = counts.get(decision, 0) + 1

    total = len(rows)
    print(f"\nSUMMARY  (total trades: {total})")
    for decision, count in counts.items():
        print(f"  {DECISION_ICON.get(decision, '      ')}{decision:<24}{count}")
    print()


def print_single_result(trade, decision, reason):
    """Print the verdict for one manually entered trade."""
    print()
    print("=" * 62)
    print("  TRADE DECISION")
    print("=" * 62)
    print(f"  Portfolio      : {trade.get('portfolio_id')}")
    print(f"  Security       : {trade.get('security_id')}")
    print(f"  Security type  : {trade.get('security_type')}")
    print(f"  Action         : {trade.get('action')}")
    print(f"  Quantity       : {trade.get('quantity')}")
    print(f"  Price          : {trade.get('price')}")
    print("-" * 62)
    print(f"  DECISION       : {decision}")
    print(f"  REASON         : {reason if reason else '(all compliance checks passed)'}")
    print("=" * 62)
    print()


def print_portfolios(portfolios):
    """Show available portfolios so the user knows valid IDs to enter."""
    header = f"{'PORTFOLIO':<12}{'NAME':<24}{'CASH':>14}{'MAX POS %':>11}  HOLDINGS"
    print()
    print(header)
    print("-" * len(header))
    for portfolio_id, portfolio in portfolios.items():
        holdings = portfolio.get("current_holdings", []) or []
        holdings_text = (
            ", ".join(
                f"{holding.get('security_id')}x{holding.get('quantity')}"
                f"@{holding.get('market_price')}"
                for holding in holdings
            )
            or "(none)"
        )
        max_pct = decimal_value(portfolio.get("max_position_pct")) or Decimal("0")
        print(
            f"{portfolio_id:<12}"
            f"{str(portfolio.get('portfolio_name', '-'))[:23]:<24}"
            f"{str(portfolio.get('cash_balance', '-')):>14}"
            f"{f'{max_pct * 100:.2f}%':>11}  "
            f"{holdings_text}"
        )
    print("-" * len(header))
    print()


# ---------------------------------------------------------------------------
# Interactive input helpers
# ---------------------------------------------------------------------------
def ask(prompt, default=None):
    """Prompt for free text; blank input falls back to default when provided."""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip()
        if answer:
            return answer
        if default is not None:
            return default
        print("  !! This field is required.")


def ask_choice(prompt, options, default=None):
    """Prompt until the user picks one of the allowed options (case-insensitive)."""
    option_text = "/".join(options)
    lookup = {option.lower(): option for option in options}
    while True:
        answer = ask(f"{prompt} ({option_text})", default)
        if answer.lower() in lookup:
            return lookup[answer.lower()]
        print(f"  !! Please enter one of: {option_text}")


def ask_number(prompt):
    """Prompt for a number; accepts anything (invalid values are a valid test case)."""
    while True:
        answer = ask(prompt)
        if decimal_value(answer) is not None:
            return answer
        # Let the user deliberately feed bad data to exercise Step 1.
        confirm = ask(f"  '{answer}' is not a number. Test it anyway? (y/n)", "n")
        if confirm.lower().startswith("y"):
            return answer


def build_manual_trade(portfolios):
    """Collect one trade interactively from the user."""
    print("\nEnter the proposed trade details (press Enter to accept [defaults]).")
    print_portfolios(portfolios)

    portfolio_ids = list(portfolios.keys())
    default_portfolio = portfolio_ids[0] if portfolio_ids else None

    portfolio_id = ask("Portfolio ID", default_portfolio)
    if portfolio_id not in portfolios:
        print(f"  !! Note: '{portfolio_id}' is not a known portfolio - "
              "it will be rejected as 'Portfolio not found.'")

    return {
        "portfolio_id": portfolio_id,
        "security_id": ask("Security ID (e.g. AAPL)").upper(),
        "action": ask_choice("Action", VALID_ACTIONS, "Buy"),
        "quantity": ask_number("Quantity"),
        "price": ask_number("Price"),
        "security_type": ask_choice("Security type", VALID_SECURITY_TYPES, "Equity"),
    }


# ---------------------------------------------------------------------------
# Menu options
# ---------------------------------------------------------------------------
def run_batch_option(portfolios, trades_path):
    """Option 1 - evaluate every trade in the test CSV."""
    print(f"\nLoading proposed trades from: {trades_path}")
    try:
        trades = load_trades(trades_path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"  !! Could not read trades file: {error}")
        return

    if not trades:
        print("  !! The trades file contains no rows.")
        return

    rows = []
    for trade in trades:
        try:
            decision, reason = decide(trade, portfolios)
        except ValueError as error:
            decision, reason = "Rejected", f"Portfolio data error: {error}"
        rows.append((trade, decision, reason))

    print_results_table(rows)


def run_manual_option(portfolios):
    """Option 2 - evaluate a single trade typed in by the user."""
    trade = build_manual_trade(portfolios)
    try:
        decision, reason = decide(trade, portfolios)
    except ValueError as error:
        decision, reason = "Rejected", f"Portfolio data error: {error}"
    print_single_result(trade, decision, reason)


MENU = """
==============================================================
  PORTFOLIO TRADE ORDER COMPLIANCE & EXECUTION CHECKER
==============================================================
  1) Run the bundled test trades file (batch check)
  2) Enter a trade manually (single check)
  3) View portfolio reference data
  4) Exit
=============================================================="""


def interactive_menu(portfolios, trades_path):
    """Main terminal menu loop."""
    while True:
        print(MENU)
        try:
            choice = input("Select an option [1-4]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting. Goodbye!")
            return

        if choice == "1":
            run_batch_option(portfolios, trades_path)
        elif choice == "2":
            try:
                run_manual_option(portfolios)
            except (EOFError, KeyboardInterrupt):
                print("\n  !! Input cancelled - returning to the menu.")
        elif choice == "3":
            print_portfolios(portfolios)
        elif choice in {"4", "q", "Q", "exit", "quit"}:
            print("\nExiting. Goodbye!")
            return
        else:
            print("  !! Invalid option. Please enter 1, 2, 3 or 4.")


def run_non_interactive(portfolios, trades_path):
    """Original CLI behaviour - plain CSV straight to stdout (pipe friendly)."""
    trades = load_trades(trades_path)
    print("portfolio_id,security_id,decision,reason")
    for trade in trades:
        decision, reason = decide(trade, portfolios)
        print(
            f"{str(trade.get('portfolio_id', '')).strip()},"
            f"{str(trade.get('security_id', '')).strip()},"
            f"{decision},{reason}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check proposed portfolio trades for compliance."
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=DEFAULT_TRADES,
        help="CSV file containing proposed trades",
    )
    parser.add_argument(
        "--portfolios",
        type=Path,
        default=DEFAULT_PORTFOLIOS,
        help="JSON file containing portfolio reference data",
    )
    parser.add_argument(
        "--no-menu",
        action="store_true",
        help="Skip the interactive menu and print raw CSV decisions to stdout",
    )
    args = parser.parse_args()

    try:
        portfolios = load_portfolios(args.portfolios)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(f"Could not load portfolios: {error}")
        return

    if args.no_menu:
        try:
            run_non_interactive(portfolios, args.trades)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            parser.error(str(error))
        return

    interactive_menu(portfolios, args.trades)


if __name__ == "__main__":
    sys.exit(main())
