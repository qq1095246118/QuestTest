"""策略详情数据聚合 Oracle，供离线用例验证核心口径。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Iterable


@dataclass(frozen=True)
class StrategyInterval:
    """策略在一个账户上的连续运行区间。"""

    strategy_id: str
    account_id: str
    interval_id: str
    start_day: Decimal
    end_day: Decimal
    profit: Decimal | None
    capital: Decimal | None
    commission: Decimal | None = Decimal("0")
    funding: Decimal | None = Decimal("0")
    exchange: str = ""
    included: bool = True
    margin_equity: Decimal | None = None
    wallet_balance: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    position_margin: Decimal | None = None
    notional: Decimal | None = None
    account_margin: Decimal | None = None
    confirmed_deposits: Decimal = Decimal("0")
    confirmed_withdrawals: Decimal = Decimal("0")

    @property
    def effective_capital(self) -> Decimal | None:
        """返回区间本金叠加已确认净资金流后的有效本金。"""

        if self.capital is None:
            return None
        return self.capital + self.confirmed_deposits - self.confirmed_withdrawals


@dataclass(frozen=True)
class StrategyMetric:
    """指标值及数据完整性状态。"""

    value: Decimal | None
    status: str
    reason_code: str | None = None
    missing_intervals: tuple[str, ...] = ()


class StrategyDetailService:
    """按策略区间计算策略详情核心数据，不依赖页面或接口。"""

    @staticmethod
    def _selected(intervals: Iterable[StrategyInterval], strategy_id: str) -> tuple[StrategyInterval, ...]:
        selected = tuple(i for i in intervals if i.included and i.strategy_id == strategy_id)
        ids = [i.interval_id for i in selected]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate interval_id")
        return selected

    @classmethod
    def amount(cls, intervals: Iterable[StrategyInterval], strategy_id: str, field: str) -> StrategyMetric:
        """汇总收益、手续费或资费，缺失时返回 partial 而不是零。"""
        if field not in {
            "profit",
            "commission",
            "funding",
            "margin_equity",
            "wallet_balance",
            "unrealized_pnl",
        }:
            raise ValueError(f"unsupported field: {field}")
        selected = cls._selected(intervals, strategy_id)
        if not selected:
            return StrategyMetric(None, "unavailable", "NO_ELIGIBLE_INTERVALS")
        values, missing = [], []
        for item in selected:
            value = getattr(item, field)
            if value is None:
                missing.append(item.interval_id)
            elif not value.is_finite():
                raise ValueError("interval values must be finite")
            else:
                values.append(value)
        known = sum(values, Decimal("0")) if values else None
        if missing:
            return StrategyMetric(known, "partial" if known is not None else "unavailable", "SOURCE_FIELD_UNAVAILABLE", tuple(sorted(missing)))
        return StrategyMetric(known, "complete")

    @classmethod
    def total_return(cls, intervals: Iterable[StrategyInterval], strategy_id: str) -> StrategyMetric:
        """使用区间收益/本金加权计算策略收益率。"""
        selected = cls._selected(intervals, strategy_id)
        if not selected:
            return StrategyMetric(None, "unavailable", "NO_ELIGIBLE_INTERVALS")
        complete, missing = [], []
        for item in selected:
            capital = item.effective_capital
            if item.profit is None or capital is None:
                missing.append(item.interval_id)
            elif not item.profit.is_finite() or not capital.is_finite():
                raise ValueError("interval values must be finite")
            else:
                complete.append(item)
        capital = sum((i.effective_capital for i in complete), Decimal("0"))
        known = None if capital <= 0 else sum((i.profit for i in complete), Decimal("0")) / capital
        if missing:
            return StrategyMetric(known, "partial" if known is not None else "unavailable", "SOURCE_FIELD_UNAVAILABLE", tuple(sorted(missing)))
        if capital <= 0:
            return StrategyMetric(None, "unavailable", "NON_POSITIVE_EFFECTIVE_CAPITAL")
        with localcontext() as context:
            context.prec = 50
            return StrategyMetric(known, "complete")

    @classmethod
    def runtime_days(cls, intervals: Iterable[StrategyInterval], strategy_id: str) -> Decimal:
        """计算所有策略区间的非重叠并集天数。"""
        spans = sorted((i.start_day, i.end_day) for i in cls._selected(intervals, strategy_id) if i.end_day > i.start_day)
        total = Decimal("0")
        current = None
        for start, end in spans:
            if current is None:
                current = [start, end]
            elif start <= current[1]:
                current[1] = max(current[1], end)
            else:
                total += current[1] - current[0]
                current = [start, end]
        if current is not None:
            total += current[1] - current[0]
        return total

    @classmethod
    def annualized_return(cls, intervals: Iterable[StrategyInterval], strategy_id: str) -> StrategyMetric:
        """用收益率除以实际运行并集天数年化，少于七天不可用。"""
        result = cls.total_return(intervals, strategy_id)
        days = cls.runtime_days(intervals, strategy_id)
        if result.value is None:
            return StrategyMetric(None, "unavailable", "TOTAL_RETURN_UNAVAILABLE")
        if days < Decimal("7"):
            return StrategyMetric(None, "unavailable", "RUNTIME_UNDER_7_DAYS")
        return StrategyMetric(result.value / days * Decimal("365"), result.status, result.reason_code, result.missing_intervals)

    @staticmethod
    def current_drawdown(equity: Iterable[Decimal]) -> StrategyMetric:
        """由策略聚合权益路径计算当前回撤。"""
        values = tuple(equity)
        if not values:
            return StrategyMetric(None, "unavailable", "NO_VALID_EQUITY_SAMPLES")
        if any(not v.is_finite() for v in values):
            raise ValueError("equity values must be finite")
        peak = max(values)
        if peak <= 0:
            return StrategyMetric(None, "unavailable", "NON_POSITIVE_EQUITY_PEAK")
        return StrategyMetric((values[-1] - peak) / peak, "complete")

    @staticmethod
    def maximum_drawdown(equity: Iterable[Decimal]) -> StrategyMetric:
        """沿时间顺序权益序列计算最大回撤，返回正比例。"""

        values = tuple(equity)
        if not values:
            return StrategyMetric(None, "unavailable", "NO_VALID_EQUITY_SAMPLES")
        if any(not value.is_finite() for value in values):
            raise ValueError("equity values must be finite")
        peak = values[0]
        maximum = Decimal("0")
        for value in values:
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak)
            peak = max(peak, value)
        if peak <= 0:
            return StrategyMetric(None, "unavailable", "NON_POSITIVE_EQUITY_PEAK")
        return StrategyMetric(maximum, "complete")

    @staticmethod
    def volatility_annualized(daily_returns: Iterable[Decimal]) -> StrategyMetric:
        """按日收益样本标准差 ddof=1 乘 sqrt(365) 计算年化波动率。"""

        values = tuple(daily_returns)
        if any(not value.is_finite() for value in values):
            raise ValueError("daily returns must be finite")
        if len(values) < 2:
            return StrategyMetric(None, "unavailable", "INSUFFICIENT_RETURN_SAMPLES")
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        with localcontext() as context:
            context.prec = 50
            return StrategyMetric(variance.sqrt() * Decimal(365).sqrt(), "complete")

    @staticmethod
    def sharpe_annualized(
        annualized_return: Decimal | None,
        annualized_volatility: Decimal | None,
    ) -> StrategyMetric:
        """用同一统计范围的年化收益和波动率计算 Sharpe。"""

        if annualized_return is None or annualized_volatility is None:
            return StrategyMetric(None, "unavailable", "RISK_INPUT_UNAVAILABLE")
        if annualized_volatility == 0:
            return StrategyMetric(None, "unavailable", "ZERO_VOLATILITY")
        return StrategyMetric(annualized_return / annualized_volatility, "complete")

    @staticmethod
    def sortino_annualized(
        page_mean: Decimal,
        daily_returns: Iterable[Decimal],
        *,
        mar: Decimal = Decimal("0"),
    ) -> StrategyMetric:
        """按 MAR 和所有有效日计算候选 Sortino；正式 MAR 口径绑定前仅作 Oracle。"""

        values = tuple(daily_returns)
        if any(not value.is_finite() for value in values):
            raise ValueError("daily returns must be finite")
        if not values:
            return StrategyMetric(None, "unavailable", "NO_VALID_RETURN_SAMPLES")
        downside = sum((min(value - mar, Decimal("0"))) ** 2 for value in values)
        with localcontext() as context:
            context.prec = 50
            deviation = (downside / Decimal(len(values))).sqrt()
            if deviation == 0:
                return StrategyMetric(None, "unavailable", "ZERO_DOWNSIDE_DEVIATION")
            return StrategyMetric(page_mean / deviation * Decimal(365).sqrt(), "complete")

    @staticmethod
    def calmar(
        annualized_return: Decimal | None,
        maximum_drawdown: Decimal | None,
    ) -> StrategyMetric:
        """按同一统计范围的年化收益率和最大回撤计算 Calmar。"""

        if annualized_return is None or maximum_drawdown is None:
            return StrategyMetric(None, "unavailable", "RISK_INPUT_UNAVAILABLE")
        if maximum_drawdown == 0:
            return StrategyMetric(None, "unavailable", "ZERO_MAXIMUM_DRAWDOWN")
        return StrategyMetric(annualized_return / maximum_drawdown, "complete")

    @staticmethod
    def position_ratio(position_margin: Decimal, account_margin: Decimal) -> StrategyMetric:
        """按候选策略口径计算持仓保证金/关联账户保证金。"""

        if account_margin <= 0:
            return StrategyMetric(None, "unavailable", "NON_POSITIVE_ACCOUNT_MARGIN")
        return StrategyMetric(position_margin / account_margin, "complete")

    @staticmethod
    def margin_usage(position_margin: Decimal, account_margin: Decimal) -> StrategyMetric:
        """按持仓保证金/账户保证金计算保证金使用率。"""

        return StrategyDetailService.position_ratio(position_margin, account_margin)

    @staticmethod
    def win_rate(completed_rounds: Iterable[Decimal]) -> StrategyMetric:
        """按已完成轮次计算候选胜率；平局规则待产品最终绑定。"""

        values = tuple(completed_rounds)
        if not values:
            return StrategyMetric(None, "unavailable", "NO_COMPLETED_ROUNDS")
        return StrategyMetric(Decimal(sum(value > 0 for value in values)) / Decimal(len(values)), "complete")

    @staticmethod
    def profit_loss_ratio(completed_rounds: Iterable[Decimal]) -> StrategyMetric:
        """按总盈利/亏损绝对值计算候选盈亏比。"""

        values = tuple(completed_rounds)
        gains = sum((value for value in values if value > 0), Decimal("0"))
        losses = sum((-value for value in values if value < 0), Decimal("0"))
        if not values:
            return StrategyMetric(None, "unavailable", "NO_COMPLETED_ROUNDS")
        if losses == 0:
            return StrategyMetric(None, "unavailable", "NO_LOSS_ROUNDS")
        return StrategyMetric(gains / losses, "complete")

    @staticmethod
    def weighted_fill_price(fills: Iterable[tuple[Decimal, Decimal]]) -> StrategyMetric:
        """按成交数量加权计算均价；数量合计为零时不可用。"""

        rows = tuple(fills)
        quantity = sum((row[0] for row in rows), Decimal("0"))
        if quantity <= 0:
            return StrategyMetric(None, "unavailable", "NON_POSITIVE_FILL_QUANTITY")
        value = sum((row[0] * row[1] for row in rows), Decimal("0")) / quantity
        return StrategyMetric(value, "complete")

    @staticmethod
    def generation_status(generations: Iterable[str]) -> StrategyMetric:
        """检查一次策略查询是否混入多个数据代次。"""

        values = {str(value) for value in generations}
        if not values:
            return StrategyMetric(None, "unavailable", "NO_GENERATION")
        if len(values) > 1:
            return StrategyMetric(None, "conflict", "MULTIPLE_GENERATIONS")
        return StrategyMetric(Decimal("1"), "complete")
