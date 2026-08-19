from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "not-ready"]
    service: Literal["asset-information-api"]
