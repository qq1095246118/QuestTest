"""账号管理总览的独立聚合 Oracle。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Iterable


@dataclass(frozen=True)
class AccountAggregationInput:
    """一个账号参与总览聚合所需的原始数值。"""

    account_id: str
    margin_equity: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
    commission_expense: Decimal | None = None
    funding_pnl: Decimal | None = None
    initial_capital: Decimal | None = None
    confirmed_deposits: Decimal = Decimal("0")
    confirmed_withdrawals: Decimal = Decimal("0")
    included: bool = True

    @property
    def effective_capital(self) -> Decimal | None:
        """返回初始本金加已确认净流量；本金缺失时返回 None。"""

        if self.initial_capital is None:
            return None
        return self.initial_capital + self.confirmed_deposits - self.confirmed_withdrawals


@dataclass(frozen=True)
class AggregateMetric:
    """聚合结果及其完整性状态。"""

    value: Decimal | None
    status: str
    reason_code: str | None = None
    known_value: Decimal | None = None
    missing_accounts: tuple[str, ...] = ()


class AccountManagementService:
    """提供不依赖页面的账号集合金额和总收益率计算。"""

    AMOUNT_FIELDS = {
        "margin_equity": "margin_equity",
        "unrealized_pnl": "unrealized_pnl",
        "realized_pnl_gross": "realized_pnl",
        "commission_expense": "commission_expense",
        "funding_pnl": "funding_pnl",
    }

    @staticmethod
    def _selected(accounts: Iterable[AccountAggregationInput]) -> tuple[AccountAggregationInput, ...]:
        """按输入顺序保留纳入集合的账号；不修改或去重调用方数据。"""

        selected = tuple(account for account in accounts if account.included)
        ids = [account.account_id for account in selected]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate account_id in aggregation input")
        return selected

    @staticmethod
    def _finite(value: Decimal | None) -> Decimal | None:
        """拒绝 NaN/Infinity；空值保持 None。"""

        if value is None:
            return None
        if not value.is_finite():
            raise ValueError("aggregation input must be finite")
        return value

    @classmethod
    def amount(cls, accounts: Iterable[AccountAggregationInput], field: str) -> AggregateMetric:
        """按选中账号汇总一个金额字段，缺失值不伪造为0。"""

        if field not in cls.AMOUNT_FIELDS:
            raise ValueError(f"unsupported account-management amount field: {field}")
        selected = cls._selected(accounts)
        if not selected:
            return AggregateMetric(None, "unavailable", "NO_ELIGIBLE_ACCOUNTS")
        values: list[tuple[str, Decimal]] = []
        missing: list[str] = []
        attribute = cls.AMOUNT_FIELDS[field]
        for account in selected:
            value = cls._finite(getattr(account, attribute))
            if value is None:
                missing.append(account.account_id)
            else:
                values.append((account.account_id, value))
        known = sum((value for _, value in values), Decimal("0")) if values else None
        if missing:
            return AggregateMetric(
                known,
                "partial" if known is not None else "unavailable",
                "SOURCE_FIELD_UNAVAILABLE",
                known,
                tuple(sorted(missing)),
            )
        return AggregateMetric(known, "complete")

    @classmethod
    def total_return(cls, accounts: Iterable[AccountAggregationInput]) -> AggregateMetric:
        """按全账号集合的 G/D 重新计算总收益率，禁止平均单账号百分比。"""

        selected = cls._selected(accounts)
        if not selected:
            return AggregateMetric(None, "unavailable", "NO_ELIGIBLE_ACCOUNTS")
        missing: list[str] = []
        complete_profits: list[Decimal] = []
        complete_capitals: list[Decimal] = []
        for account in selected:
            profit = cls._finite(account.realized_pnl)
            capital = cls._finite(account.effective_capital)
            if profit is None or capital is None:
                missing.append(account.account_id)
                continue
            complete_profits.append(profit)
            complete_capitals.append(capital)
        if missing:
            known = cls._partial_total_return(complete_profits, complete_capitals)
            return AggregateMetric(
                known,
                "partial" if known is not None else "unavailable",
                "SOURCE_FIELD_UNAVAILABLE",
                known,
                tuple(sorted(missing)),
            )
        total_profit = sum(complete_profits, Decimal("0"))
        total_capital = sum(complete_capitals, Decimal("0"))
        if total_capital <= 0:
            return AggregateMetric(None, "unavailable", "NON_POSITIVE_EFFECTIVE_CAPITAL")
        with localcontext() as context:
            context.prec = 50
            return AggregateMetric(total_profit / total_capital, "complete")

    @classmethod
    def _partial_total_return(
        cls,
        profits: Iterable[Decimal],
        capitals: Iterable[Decimal],
    ) -> Decimal | None:
        """计算已知分量的诊断值；它不代表完整总收益率。"""

        total_capital = sum(capitals, Decimal("0"))
        if total_capital <= 0:
            return None
        return sum(profits, Decimal("0")) / total_capital

    @staticmethod
    def annualized_return(total_return: Decimal | None, runtime_days: Decimal) -> AggregateMetric:
        """按聚合总收益率和有效运行天数计算年化收益率。"""

        if total_return is None:
            return AggregateMetric(None, "unavailable", "TOTAL_RETURN_UNAVAILABLE")
        if not runtime_days.is_finite():
            raise ValueError("runtime_days must be finite")
        if runtime_days < Decimal("7"):
            return AggregateMetric(None, "unavailable", "RUNTIME_UNDER_7_DAYS")
        if runtime_days <= 0:
            return AggregateMetric(None, "unavailable", "NON_POSITIVE_RUNTIME")
        with localcontext() as context:
            context.prec = 50
            return AggregateMetric(total_return / runtime_days * Decimal("365"), "complete")

    @staticmethod
    def current_drawdown(equity_values: Iterable[Decimal]) -> AggregateMetric:
        """按聚合权益序列的历史峰值计算当前回撤，结果为非正比例。"""

        values = tuple(equity_values)
        if not values:
            return AggregateMetric(None, "unavailable", "NO_VALID_EQUITY_SAMPLES")
        if any(not value.is_finite() for value in values):
            raise ValueError("equity values must be finite")
        peak = max(values)
        if peak <= 0:
            return AggregateMetric(None, "unavailable", "NON_POSITIVE_EQUITY_PEAK")
        with localcontext() as context:
            context.prec = 50
            return AggregateMetric((values[-1] - peak) / peak, "complete")

    @classmethod
    def from_strings(cls, value: str | int | float | Decimal | None) -> Decimal | None:
        """把 JSON/数据库数字安全转换为 Decimal，供 fixture 解析使用。"""

        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError(f"invalid decimal value: {value!r}") from error
        return cls._finite(parsed)
