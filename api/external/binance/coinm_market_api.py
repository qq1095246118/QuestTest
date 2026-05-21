"""Binance COIN-M 外部原始 API 调用封装。

本模块只负责拼接请求参数并发起 HTTP 调用，不做业务判断或断言。
"""

from api.base_api import BaseAPI
from config.settings import settings

class COINMMarketAPI(BaseAPI):
    """
    Binance 币本位合约行情接口 (COIN-M Futures Market Data API)
    Base URL: https://dapi.binance.com
    """
    def __init__(self):
        super().__init__(base_url=settings.binance_coinm_url)

    def ping(self):
        """测试服务器连通性"""
        return self.get("/dapi/v1/ping")

    def get_server_time(self):
        """获取服务器时间"""
        return self.get("/dapi/v1/time")

    def get_exchange_info(self):
        """获取交易规则和交易对信息"""
        return self.get("/dapi/v1/exchangeInfo")

    def get_depth(self, symbol: str, limit: int = 100):
        """获取深度信息 (Order Book)"""
        params = {"symbol": symbol, "limit": limit}
        return self.get("/dapi/v1/depth", params=params)

    def get_recent_trades(self, symbol: str, limit: int = 500):
        """近期成交列表"""
        params = {"symbol": symbol, "limit": limit}
        return self.get("/dapi/v1/trades", params=params)

    def get_historical_trades(self, symbol: str, limit: int = 500, fromId: int = None):
        """查询历史成交 (需要API Key)"""
        params = {"symbol": symbol, "limit": limit}
        if fromId:
            params["fromId"] = fromId
        return self.get("/dapi/v1/historicalTrades", params=params)

    def get_agg_trades(self, symbol: str, fromId: int = None, startTime: int = None, endTime: int = None, limit: int = 500):
        """近期汇总成交列表"""
        params = {"symbol": symbol, "limit": limit}
        if fromId: params["fromId"] = fromId
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/aggTrades", params=params)

    def get_klines(self, symbol: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """K线数据"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/klines", params=params)

    def get_continuous_klines(self, pair: str, contractType: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """连续合约K线数据"""
        params = {"pair": pair, "contractType": contractType, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/continuousKlines", params=params)

    def get_index_price_klines(self, pair: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """指数价格K线数据"""
        params = {"pair": pair, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/indexPriceKlines", params=params)

    def get_mark_price_klines(self, symbol: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """标记价格K线数据"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/markPriceKlines", params=params)

    def get_premium_index_klines(self, symbol: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """溢价指数K线数据"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/premiumIndexKlines", params=params)

    def get_premium_index(self, symbol: str = None, pair: str = None):
        """最新标记价格和资金费率"""
        params = {}
        if symbol: params["symbol"] = symbol
        if pair: params["pair"] = pair
        return self.get("/dapi/v1/premiumIndex", params=params)

    def get_funding_rate(self, symbol: str = None, startTime: int = None, endTime: int = None, limit: int = 100):
        """查询资金费率历史"""
        params = {"limit": limit}
        if symbol: params["symbol"] = symbol
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/dapi/v1/fundingRate", params=params)

    def get_ticker_24hr(self, symbol: str = None, pair: str = None):
        """24小时价格变动情况"""
        params = {}
        if symbol: params["symbol"] = symbol
        if pair: params["pair"] = pair
        return self.get("/dapi/v1/ticker/24hr", params=params)

    def get_ticker_price(self, symbol: str = None, pair: str = None):
        """最新价格"""
        params = {}
        if symbol: params["symbol"] = symbol
        if pair: params["pair"] = pair
        return self.get("/dapi/v1/ticker/price", params=params)

    def get_book_ticker(self, symbol: str = None, pair: str = None):
        """当前最优挂单"""
        params = {}
        if symbol: params["symbol"] = symbol
        if pair: params["pair"] = pair
        return self.get("/dapi/v1/ticker/bookTicker", params=params)

    def get_open_interest(self, symbol: str):
        """获取当前未平仓合约数"""
        return self.get("/dapi/v1/openInterest", params={"symbol": symbol})

    def get_open_interest_hist(self, pair: str, contractType: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """合约持仓量历史"""
        params = {"pair": pair, "contractType": contractType, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/openInterestHist", params=params)

    def get_top_long_short_account_ratio(self, pair: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """大户账户数多空比"""
        params = {"pair": pair, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/topLongShortAccountRatio", params=params)

    def get_top_long_short_position_ratio(self, pair: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """大户持仓量多空比"""
        params = {"pair": pair, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/topLongShortPositionRatio", params=params)

    def get_global_long_short_account_ratio(self, pair: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """多空持仓人数比"""
        params = {"pair": pair, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/globalLongShortAccountRatio", params=params)

    def get_taker_buy_sell_vol(self, pair: str, contractType: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """合约主动买卖量"""
        params = {"pair": pair, "contractType": contractType, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/takerBuySellVol", params=params)

    def get_basis(self, pair: str, contractType: str, period: str, limit: int = 30, startTime: int = None, endTime: int = None):
        """基差"""
        params = {"pair": pair, "contractType": contractType, "period": period, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/futures/data/basis", params=params)
