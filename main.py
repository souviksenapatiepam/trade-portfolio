import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def decimal_value(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def evaluate_trade(trade, portfolio):
    quantity = decimal_value(trade.get("quantity"))
    price = decimal_value(trade.get("price"))
    action = str(trade.get("action", "")).strip()

    if quantity is None or price is None or quantity <= 0 or price <= 0:
        return "Rejected", "Invalid trade data."
    if action not in {"Buy", "Sell"}:
        return "Rejected", "Invalid trade data."

    security_id = str(trade.get("security_id", "")).strip()
    if security_id in set(portfolio.get("restricted_securities", []) or []):
        return "Rejected", "Security is restricted for this portfolio."

    holdings = portfolio.get("current_holdings", []) or []
    holdings_by_security = {
        str(holding["security_id"]).strip(): holding
        for holding in holdings
    }

    if action == "Sell":
        holding = holdings_by_security.get(security_id)
        existing_quantity = (
            decimal_value(holding["quantity"]) if holding is not None else Decimal("0")
        )
        if existing_quantity is None or existing_quantity < quantity:
            return "Rejected", "Insufficient holdings to sell."
        return "Approved", ""

    cash_balance = decimal_value(portfolio.get("cash_balance"))
    if cash_balance is None:
        raise ValueError("Portfolio cash_balance is missing or invalid.")

    trade_cost = quantity * price
    if trade_cost > cash_balance:
        return "Rejected", "Insufficient cash for this buy order."

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
            "Exceeds maximum position concentration — requires approval.",
        )
    return "Approved", ""


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Check proposed portfolio trades for compliance."
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=base_dir / "trades_test.csv",
        help="CSV file containing proposed trades",
    )
    parser.add_argument(
        "--portfolios",
        type=Path,
        required=True,
        help="JSON file containing portfolio reference data",
    )
    args = parser.parse_args()

    try:
        with args.portfolios.open("r", encoding="utf-8") as file:
            portfolio_data = json.load(file)

        if isinstance(portfolio_data, dict) and "portfolios" in portfolio_data:
            portfolio_data = portfolio_data["portfolios"]

        if isinstance(portfolio_data, list):
            portfolios = {
                str(portfolio["portfolio_id"]).strip(): portfolio
                for portfolio in portfolio_data
                if isinstance(portfolio, dict) and "portfolio_id" in portfolio
            }
        elif isinstance(portfolio_data, dict):
            portfolios = portfolio_data
        else:
            parser.error("Portfolio JSON must contain an object or a list.")

        with args.trades.open("r", encoding="utf-8-sig", newline="") as file:
            trades = list(csv.DictReader(file))

        # Convert the CSV rows to JSON-compatible dictionaries in memory.
        trade_json = json.loads(json.dumps(trades))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(str(error))

    print("portfolio_id,security_id,decision,reason")
    for trade in trade_json:
        portfolio_id = str(trade.get("portfolio_id", "")).strip()
        security_id = str(trade.get("security_id", "")).strip()

        if portfolio_id not in portfolios:
            decision, reason = "Rejected", "Portfolio not found."
        else:
            try:
                decision, reason = evaluate_trade(trade, portfolios[portfolio_id])
            except ValueError as error:
                parser.error(str(error))

        print(f"{portfolio_id},{security_id},{decision},{reason}")


if __name__ == "__main__":
    main()
