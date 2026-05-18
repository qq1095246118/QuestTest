from api_services.base_api import BaseAPI
from config.settings import settings

class SpotMarketAPI(BaseAPI):
    """
    Binance 现货行情接口 (Spot Market Data API)
    Base URL: https://api.binance.com
    """
    def __init__(self):
        super().__init__(base_url=settings.binance_spot_url)

    def ping(self):
        """测试服务器连通性"""
        return self.get("/api/v3/ping")

    def get_server_time(self):
        """获取服务器时间"""
        return self.get("/api/v3/time")

    def get_exchange_info(self, symbol=None, symbols=None):
        """获取交易规则和交易对信息"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        elif symbols:
            # symbols must be a JSON array string e.g. '["BTCUSDT","BNBUSDT"]'
            params["symbols"] = symbols
        return self.get("/api/v3/exchangeInfo", params=params)

    def get_depth(self, symbol: str, limit: int = 100):
        """获取深度信息 (Order Book)"""
        params = {"symbol": symbol, "limit": limit}
        return self.get("/api/v3/depth", params=params)

    def get_recent_trades(self, symbol: str, limit: int = 500):
        """近期成交列表"""
        params = {"symbol": symbol, "limit": limit}
        return self.get("/api/v3/trades", params=params)

    def get_historical_trades(self, symbol: str, limit: int = 500, fromId: int = None):
        """查询历史成交 (需要API Key)"""
        params = {"symbol": symbol, "limit": limit}
        if fromId:
            params["fromId"] = fromId
        return self.get("/api/v3/historicalTrades", params=params)

    def get_agg_trades(self, symbol: str, fromId: int = None, startTime: int = None, endTime: int = None, limit: int = 500):
        """近期汇总成交列表"""
        params = {"symbol": symbol, "limit": limit}
        if fromId: params["fromId"] = fromId
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/api/v3/aggTrades", params=params)

    def get_klines(self, symbol: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """K线数据 (Kline/Candlestick Data)"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/api/v3/klines", params=params)

    def get_ui_klines(self, symbol: str, interval: str, startTime: int = None, endTime: int = None, limit: int = 500):
        """UI K线数据 (UI Kline)"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if startTime: params["startTime"] = startTime
        if endTime: params["endTime"] = endTime
        return self.get("/api/v3/uiKlines", params=params)

    def get_avg_price(self, symbol: str):
        """当前平均价格"""
        return self.get("/api/v3/avgPrice", params={"symbol": symbol})

    def get_ticker_24hr(self, symbol: str = None, symbols: str = None, type: str = "FULL"):
        """24小时价格变动情况"""
        params = {"type": type}
        if symbol: params["symbol"] = symbol
        if symbols: params["symbols"] = symbols
        return self.get("/api/v3/ticker/24hr", params=params)

    def get_ticker_price(self, symbol: str = None, symbols: str = None):
        """最新价格"""
        params = {}
        if symbol: params["symbol"] = symbol
        if symbols: params["symbols"] = symbols
        return self.get("/api/v3/ticker/price", params=params)

    def get_book_ticker(self, symbol: str = None, symbols: str = None):
        """当前最优挂单"""
        params = {}
        if symbol: params["symbol"] = symbol
        if symbols: params["symbols"] = symbols
        return self.get("/api/v3/ticker/bookTicker", params=params)

    def get_rolling_window_ticker(self, symbol: str = None, symbols: str = None, windowSize: str = "1d", type: str = "FULL"):
        """滚动窗口价格变动统计"""
        params = {"windowSize": windowSize, "type": type}
        if symbol: params["symbol"] = symbol
        if symbols: params["symbols"] = symbols
        return self.get("/api/v3/ticker", params=params)
