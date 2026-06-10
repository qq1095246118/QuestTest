from config.settings import settings


class TestSettingsConfig:
    """测试因子库自动化运行配置。

    请求参数:
        读取 config/env.<env> 和环境变量生成的 settings 实例。
    返回值:
        无返回值；pytest 根据配置字段是否存在判断配置模型是否满足自动化需求。
    """

    def test_exchange_test_config_fields_exist(self):
        """验证交易所接口测试配置字段已在 settings 中声明。

        请求参数:
            无，直接读取 settings.exchange_test_* 字段。
        返回值:
            settings 应提供交易所、账户类型、API key、API secret 和 passphrase 字段。
        """
        assert isinstance(settings.exchange_test_exchange, str)
        assert isinstance(settings.exchange_test_account_type, str)
        assert isinstance(settings.exchange_test_api_key, str)
        assert isinstance(settings.exchange_test_api_secret, str)
        assert isinstance(settings.exchange_test_api_passphrase, str)

    def test_factor_notification_webhook_secret_field_exists(self):
        """验证因子挖掘通知接口 webhook secret 配置字段已在 settings 中声明。

        请求参数:
            无，直接读取 settings.factor_webhook_secret 字段。
        返回值:
            settings 应提供 factor_webhook_secret 字段，未配置时为空字符串。
        """
        assert isinstance(settings.factor_webhook_secret, str)

    def test_exchange_test_config_fixture_skips_when_credentials_missing(self, request):
        """验证交易所正向用例配置 fixture 在凭证缺失时会跳过。

        请求参数:
            request: pytest request 对象，用于动态获取 exchange_test_config fixture。
        返回值:
            凭证缺失时应触发 pytest.skip；凭证完整时应返回包含 exchange 和 api_key 的字典。
        """
        config = request.getfixturevalue("exchange_test_config")

        assert config["exchange"]
        assert config["api_key"]
