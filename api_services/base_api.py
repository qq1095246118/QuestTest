from infrastructure.http.http_client import HTTPClient
from config.settings import settings

class BaseAPI:
    def __init__(self, base_url=None):
        self.base_url = base_url or settings.base_url
        self.headers = {
            "Content-Type": "application/json"
        }
        # 币安接口推荐在 header 传递 API-Key 以获得更高的限流额度（即便是公开行情接口）
        if settings.api_key:
            self.headers["X-MBX-APIKEY"] = settings.api_key

    def get(self, endpoint: str, params=None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("GET", url, headers=self.headers, params=params)
    
    def post(self, endpoint: str, json=None, data=None):
        url = f"{self.base_url}{endpoint}"
        return HTTPClient.request("POST", url, headers=self.headers, json=json, data=data)
