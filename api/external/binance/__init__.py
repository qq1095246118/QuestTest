"""Binance 外部原始行情 API 封装。"""

from .coinm_market_api import COINMMarketAPI
from .spot_market_api import SpotMarketAPI
from .usdm_market_api import USDMMarketAPI

__all__ = ["SpotMarketAPI", "USDMMarketAPI", "COINMMarketAPI"]
