"""账户详情公式 Oracle 和响应契约编排。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from tools.http_response import read_json_object


@dataclass(frozen=True)
class AccountSummaryContract:
    """保存摘要响应中已核对的身份和快照元数据。"""

    account_id: str
    snapshot_id: str | None
    generation: str | None
    mock: bool | None


class AccountDetailService:
    """提供不执行断言的账户详情业务编排和独立公式计算。"""

    @staticmethod
    def total_return(
        eligible_profit: Decimal | None,
        initial_capital: Decimal | None,
        confirmed_deposits: Decimal = Decimal("0"),
        confirmed_withdrawals: Decimal = Decimal("0"),
    ) -> Decimal | None:
        """按账户全历史 G/D 计算总收益率；缺失或零本金返回 None。"""

        if eligible_profit is None or initial_capital is None:
            return None
        denominator = initial_capital + confirmed_deposits - confirmed_withdrawals
        if denominator <= 0:
            return None
        return eligible_profit / denominator

    @staticmethod
    def annualized_return(total_return: Decimal | None, runtime_days: Decimal) -> Decimal | None:
        """按总收益率/实际运行天数×365计算年化；不足7天或无效时长返回 None。"""

        if total_return is None or runtime_days < Decimal("7"):
            return None
        if runtime_days <= 0:
            return None
        return total_return / runtime_days * Decimal("365")

    @staticmethod
    def sample_volatility_annualized(daily_returns: Iterable[Decimal]) -> Decimal | None:
        """按 n-1 样本标准差并乘 sqrt(365) 计算年化波动率。"""

        values = tuple(daily_returns)
        if len(values) < 2:
            return None
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        with localcontext() as context:
            context.prec = 50
            return variance.sqrt() * Decimal(365).sqrt()

    @staticmethod
    def sortino_annualized(
        page_mean: Decimal,
        daily_returns: Iterable[Decimal],
        *,
        mar: Decimal = Decimal("0"),
    ) -> Decimal | None:
        """按页面收益均值、MAR=0 和全部有效日数计算年化 Sortino。"""

        values = tuple(daily_returns)
        if not values:
            return None
        downside = sum((min(value - mar, Decimal("0"))) ** 2 for value in values)
        with localcontext() as context:
            context.prec = 50
            deviation = (downside / Decimal(len(values))).sqrt()
        if deviation == 0:
            return None
        with localcontext() as context:
            context.prec = 50
            return page_mean / deviation * Decimal(365).sqrt()

    @staticmethod
    def maximum_drawdown(nav_values: Iterable[Decimal]) -> Decimal | None:
        """按历史峰值计算最大回撤；没有有效净值时返回 None。"""

        values = tuple(nav_values)
        if not values:
            return None
        peak = values[0]
        maximum = Decimal("0")
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak)
        return maximum

    @staticmethod
    def calmar(annualized: Decimal | None, maximum_drawdown: Decimal | None) -> Decimal | None:
        """按同一统计范围的年化收益/最大回撤计算 Calmar。"""

        if annualized is None or maximum_drawdown in (None, Decimal("0")):
            return None
        with localcontext() as context:
            context.prec = 50
            return annualized / maximum_drawdown

    @staticmethod
    def win_rate(completed_rounds: Iterable[Decimal]) -> Decimal | None:
        """按已完成交易轮次中净收益大于零的数量计算胜率。"""

        values = tuple(completed_rounds)
        if not values:
            return None
        return Decimal(sum(value > 0 for value in values)) / Decimal(len(values))

    @staticmethod
    def profit_loss_ratio(completed_rounds: Iterable[Decimal]) -> Decimal | None:
        """按总盈利/总亏损绝对值计算盈亏比。"""

        values = tuple(completed_rounds)
        if not values:
            return None
        gains = sum((value for value in values if value > 0), Decimal("0"))
        losses = sum((-value for value in values if value < 0), Decimal("0"))
        if losses == 0:
            return None
        return gains / losses

    @staticmethod
    def current_position_ratio(notional: Decimal, account_margin: Decimal) -> Decimal | None:
        """按持仓名义价值/账户保证金计算当前仓位。"""

        return None if account_margin <= 0 else notional / account_margin

    @staticmethod
    def margin_usage_ratio(position_margin: Decimal, account_margin: Decimal) -> Decimal | None:
        """按持仓保证金/账户保证金计算保证金使用率。"""

        return None if account_margin <= 0 else position_margin / account_margin

    @staticmethod
    def profit_share_receivable(eligible_profit: Decimal, share_rate: Decimal) -> Decimal:
        """按冻结周期合格利润和分润比例计算应收金额。"""

        if eligible_profit < 0:
            raise ValueError("eligible_profit must not be negative")
        if share_rate < 0 or share_rate > 1:
            raise ValueError("share_rate must be between 0 and 1")
        return eligible_profit * share_rate

    @staticmethod
    def successor_cycle_capital(
        trigger_wallet: Decimal,
        confirmed_deposits: Decimal = Decimal("0"),
        confirmed_withdrawals: Decimal = Decimal("0"),
    ) -> Decimal:
        """按触发钱包和后继周期真实净流量计算新周期本金。"""

        return trigger_wallet + confirmed_deposits - confirmed_withdrawals

    @staticmethod
    def receipt_state(receivable: Decimal, received: Decimal) -> tuple[str, Decimal]:
        """返回未收/部分/已收状态及待收金额，拒绝负数和超收。"""

        if receivable < 0 or received < 0 or received > receivable:
            raise ValueError("receipt amount is outside the bill boundary")
        remaining = receivable - received
        if received == 0:
            state = "UNPAID"
        elif remaining == 0:
            state = "PAID"
        else:
            state = "PARTIAL"
        return state, remaining

    @staticmethod
    def parse_summary_contract(
        response: Any,
        *,
        expected_account_id: int | str,
        expected_snapshot_id: str | None = None,
    ) -> tuple[AccountSummaryContract, dict[str, Any]]:
        """解析摘要响应并校验账户/快照身份，不替换缺失数据。"""

        body = read_json_object(response, "account detail summary")
        if body.get("success") is not True:
            raise ValueError("account detail summary business response is not successful")
        data = body.get("data")
        meta = body.get("meta")
        if not isinstance(data, Mapping) or not isinstance(meta, Mapping):
            raise ValueError("account detail summary must contain data and meta objects")
        context = data.get("context")
        subject_id = context.get("subject_id") if isinstance(context, Mapping) else None
        if str(subject_id) != str(expected_account_id):
            raise ValueError("summary subject_id does not match requested account")
        snapshot_id = meta.get("snapshot_id")
        if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
            raise ValueError("summary snapshot_id does not match fixture")
        contract = AccountSummaryContract(
            account_id=str(subject_id),
            snapshot_id=str(snapshot_id) if snapshot_id is not None else None,
            generation=str(meta["generation"]) if meta.get("generation") is not None else None,
            mock=meta.get("mock") if isinstance(meta.get("mock"), bool) else None,
        )
        return contract, dict(data)
