from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sportscards:sportscards@localhost:5432/sportscards"

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "PRODUCTION"

    psa_token: str = ""

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    @property
    def ebay_oauth_url(self) -> str:
        if self.ebay_env == "SANDBOX":
            return "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
        return "https://api.ebay.com/identity/v1/oauth2/token"

    @property
    def ebay_api_base(self) -> str:
        if self.ebay_env == "SANDBOX":
            return "https://api.sandbox.ebay.com"
        return "https://api.ebay.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
