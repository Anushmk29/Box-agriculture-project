import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    PROJECT_NAME: str = "Fresh Produce AI Order-to-Cash Platform"
    API_V3_STR: str = "/api/v3"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "local-development-key-change-before-deploying")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours

    DATABASE_URL: str = Field(
        validation_alias=AliasChoices("DATABASE_URL", "SUPABASE_DATABASE_URL"),
    )
    PUBLIC_APP_URL: str = os.getenv("PUBLIC_APP_URL", "http://localhost:3000")
    INVOICE_BANK_NAME: str = os.getenv("INVOICE_BANK_NAME", "")
    INVOICE_BANK_ACCOUNT_NAME: str = os.getenv("INVOICE_BANK_ACCOUNT_NAME", "")
    INVOICE_BANK_SWIFT: str = os.getenv("INVOICE_BANK_SWIFT", "")
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]

    # Disable only during a staged migration of legacy packing data.
    PACKING_PROFILES_ENABLED: bool = True
    EXCEL_TEMPLATE_PROFILES_ENABLED: bool = True
    FILE_STORAGE_PROVIDER: Literal["local", "supabase"] = "local"
    SUPABASE_URL: str = ""
    SUPABASE_STORAGE_KEY: SecretStr = SecretStr("")
    SUPABASE_STORAGE_BUCKET: str = "order-sources"
    LOCAL_STORAGE_ROOT: str = str(Path(__file__).resolve().parents[2] / "uploads")
    LEGACY_UPLOAD_ROOT: str = str(Path(__file__).resolve().parents[2] / "uploads")
    # Keep off during restores and until an operator verifies the full reference inventory.
    STORAGE_GC_ENABLED: bool = False

    # Invariant enforcement
    INVARIANT_INVOICE_FINAL_PACKING_ONLY: bool = True
    INVARIANT_RECEIPT_AFTER_PAYMENT_ONLY: bool = True

    @model_validator(mode="after")
    def reject_development_secrets_outside_development(self):
        bank_values = (
            self.INVOICE_BANK_NAME.strip(),
            self.INVOICE_BANK_ACCOUNT_NAME.strip(),
            self.INVOICE_BANK_SWIFT.strip(),
        )
        if any(bank_values) and not all(bank_values):
            raise ValueError(
                "Invoice bank settings must provide bank name, account name, and SWIFT together"
            )
        if self.ENVIRONMENT.lower() not in {"development", "test"}:
            if (
                self.SECRET_KEY == "local-development-key-change-before-deploying"
                or len(self.SECRET_KEY) < 32
            ):
                raise ValueError(
                    "Production requires a unique SECRET_KEY with at least 32 characters"
                )
            if not self.PUBLIC_APP_URL.startswith("https://"):
                raise ValueError("Production PUBLIC_APP_URL must use HTTPS")
            if self.FILE_STORAGE_PROVIDER != "supabase":
                raise ValueError("Production requires private Supabase Storage")
        if self.FILE_STORAGE_PROVIDER == "supabase":
            if not self.SUPABASE_URL or not self.SUPABASE_STORAGE_KEY.get_secret_value():
                raise ValueError("Supabase Storage requires SUPABASE_URL and a server-only SUPABASE_STORAGE_KEY")
            api = urlsplit(self.SUPABASE_URL)
            if (api.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.supabase\.co", api.netloc)
                    or api.path not in {"", "/"} or api.query or api.fragment):
                raise ValueError("Use the project's HTTPS Supabase API origin")
            db = make_url(self.DATABASE_URL)
            reference = ((db.username or "").removeprefix("postgres.") if (db.username or "").startswith("postgres.")
                         else (db.host or "").removeprefix("db.").removesuffix(".supabase.co"))
            if reference != api.netloc.removesuffix(".supabase.co"):
                raise ValueError("Supabase Database and Storage must use the same project")
        return self


settings = Settings()
