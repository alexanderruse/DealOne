from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rules(StrictModel):
    product_id: str = Field(min_length=1, max_length=64)
    max_total_cents: int = Field(gt=0, le=1_000_000)
    expires_at: datetime
    quantity: Literal[1] = 1


class ActivateRequest(StrictModel):
    checkout_mode: Literal["merchant_handoff", "sandbox"] = "merchant_handoff"
    sandbox_total_cents: int | None = Field(default=None, gt=0, le=1_000_000)
    # Default is ask-first: activation watches, it does not auto-buy.
    purchase_mode: Literal["ask_first", "auto_buy"] = "ask_first"


class ParseRequest(StrictModel):
    text: str = Field(min_length=5, max_length=2000)


class AssistantRequest(ParseRequest):
    pass


class PermissionReference(StrictModel):
    permission_id: str


class ProductReference(StrictModel):
    product_id: str


class SearchRequest(StrictModel):
    query: str = Field(max_length=150)


class PriceChange(StrictModel):
    price_cents: int = Field(gt=0, le=1_000_000)
    shipping_cents: int | None = Field(default=None, ge=0, le=100_000)


class CreditLimitChange(StrictModel):
    available_cents: int = Field(default=0, ge=0, le=1_000_000)
    permission_id: str | None = None
    account_id: str | None = None
