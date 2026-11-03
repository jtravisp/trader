"""Broker access for the risk gateway. CLAUDE.md rule 1: this package is the ONLY place that may
call Alpaca order endpoints. Uses the TRADE key pair (ALPACA_TRADE_*), never the data keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trader.config import secret


@dataclass(frozen=True)
class Account:
    equity: float
    last_equity: float  # equity at the previous close (Alpaca)
    cash: float
    buying_power: float  # non-marginable buying power (cash account)


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    market_value: float


@dataclass(frozen=True)
class BrokerOrder:
    broker_id: str
    client_order_id: str
    symbol: str
    side: str
    status: str
    notional: float | None
    qty: float | None
    filled_qty: float
    filled_avg_price: float | None


class Broker(Protocol):
    def account(self) -> Account: ...
    def positions(self) -> dict[str, Position]: ...
    def open_orders(self) -> list[BrokerOrder]: ...
    def orders_since(self, iso_ts: str) -> list[BrokerOrder]: ...
    def order_by_client_id(self, client_order_id: str) -> BrokerOrder | None: ...
    def submit(self, client_order_id: str, symbol: str, side: str, notional: float | None, qty: float | None) -> BrokerOrder: ...
    def cancel_all(self) -> int: ...
    def close_all(self) -> int: ...
    def market_open(self) -> bool: ...


def _order(o: dict) -> BrokerOrder:
    f = lambda k: float(o[k]) if o.get(k) not in (None, "") else None
    return BrokerOrder(str(o["id"]), o.get("client_order_id", ""), o["symbol"], o["side"], o["status"],
                       f("notional"), f("qty"), f("filled_qty") or 0.0, f("filled_avg_price"))


class AlpacaBroker:
    """alpaca-py TradingClient with raw_data=True (its enums lag new exchanges, e.g. TXSE)."""

    def __init__(self) -> None:
        from alpaca.trading.client import TradingClient

        paper = secret("ALPACA_TRADE_PAPER").lower() != "false"
        self.paper = paper
        self.tc = TradingClient(secret("ALPACA_TRADE_KEY"), secret("ALPACA_TRADE_SECRET"), paper=paper, raw_data=True)

    def account(self) -> Account:
        a = self.tc.get_account()
        return Account(float(a["equity"]), float(a["last_equity"]), float(a["cash"]),
                       float(a.get("non_marginable_buying_power") or a["cash"]))

    def positions(self) -> dict[str, Position]:
        return {p["symbol"]: Position(p["symbol"], float(p["qty"]), float(p["market_value"])) for p in self.tc.get_all_positions()}

    def open_orders(self) -> list[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        return [_order(o) for o in self.tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))]

    def orders_since(self, iso_ts: str) -> list[BrokerOrder]:
        from datetime import datetime

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, after=datetime.fromisoformat(iso_ts), limit=500)
        return [_order(o) for o in self.tc.get_orders(req)]

    def order_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        try:
            return _order(self.tc.get_order_by_client_id(client_order_id))
        except Exception:  # alpaca raises APIError 404 when unknown
            return None

    def submit(self, client_order_id: str, symbol: str, side: str, notional: float | None, qty: float | None) -> BrokerOrder:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol, side=OrderSide.BUY if side == "buy" else OrderSide.SELL, time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
            **({"notional": round(notional, 2)} if notional is not None else {"qty": qty}),
        )
        return _order(self.tc.submit_order(req))

    def cancel_all(self) -> int:
        return len(self.tc.cancel_orders() or [])

    def close_all(self) -> int:
        return len(self.tc.close_all_positions(cancel_orders=True) or [])

    def market_open(self) -> bool:
        return bool(self.tc.get_clock()["is_open"])
