---
name: fastapi-service
description: Structure a FastAPI service with thin routers, typed services that hold the logic, one settings object,
  Depends for dependencies, resources opened in the lifespan and errors mapped in one place. Use it when you start a
  FastAPI project, add an endpoint or service, or find logic in a route.
license: MIT
metadata:
  sources: wshobson/plugins/api-scaffolding/skills/fastapi-templates, own
---

# FastAPI service

Keep HTTP at the edge. A router turns a request into a call and the result into a response; everything that
decides something lives in a service that knows nothing about HTTP. Then a CLI, a scheduled job and a test call the
same code, and the router stays a few lines.

## 1. Layout

Group by feature, not by kind of file, once there is more than one feature:

```
app/
  main.py            creates the app, opens resources in the lifespan, includes routers, registers error handlers
  settings.py        the one Settings class
  services.py        the typed object that holds the shared services
  orders/
    router.py        HTTP only
    dependencies.py  the Depends functions that hand a router its service
    schemas.py       request and response models
    service.py       the logic and its domain errors
    store.py         database access
tests/
```

Nothing outside a router imports from a router, and a service never imports from `fastapi`.

## 2. Settings, read once

```python
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env")

    database_url: str
    payment_api_key: SecretStr
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

The app fails at start when a required value is missing, instead of at the first request that needs it. `SecretStr`
keeps the key out of logs and reprs. Never read `os.environ` elsewhere; document every variable in `.env.example`
and commit no populated `.env`.

## 3. A service with typed dependencies

```python
from dataclasses import dataclass
from typing import Literal

from app.orders.store import OrderStore


class OrderNotFound(Exception):
    def __init__(self, order_id: int) -> None:
        super().__init__(f"There is no order {order_id}.")
        self.order_id = order_id


@dataclass(frozen=True)
class Order:
    id: int
    status: Literal["open", "paid", "cancelled"]
    total_cents: int


class OrderService:
    def __init__(self, store: OrderStore) -> None:
        self._store = store

    async def get(self, order_id: int) -> Order:
        order = await self._store.find(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        return order
```

Domain errors say what happened in domain words. The service takes its collaborators as parameters, which is what
makes it testable without patching.

## 4. A thin router

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.orders.dependencies import get_order_service
from app.orders.schemas import OrderOut
from app.orders.service import OrderNotFound, OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]


@router.get("/{order_id}")
async def read_order(order_id: int, service: OrderServiceDep) -> OrderOut:
    try:
        order = await service.get(order_id)
    except OrderNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return OrderOut.model_validate(order, from_attributes=True)
```

The return annotation is the response model, so the OpenAPI document and the validation follow from it. When many
routes map the same domain error, register one exception handler in `main.py` instead of repeating the `try`.

## 5. Resources in the lifespan

Open shared resources (the database engine, an HTTP client) once at start and close them at shutdown:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import Services
from app.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_async_engine(get_settings().database_url)
    app.state.services = Services.build(engine)
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
```

Keep shared state in one typed object (`Services` here), not in loose attributes on `app.state`. The dependency
functions read it from `request.app.state.services` and return the typed service.

## 6. Async without blocking

An `async def` route runs on the event loop: a blocking call inside it (a synchronous database driver, `requests`,
`time.sleep`, heavy CPU work) stalls every other request. Use async libraries in async routes, or declare the route
with plain `def` so FastAPI runs it in a thread pool. Give every outgoing call a timeout.

## 7. Schemas at the edge

Request and response models live in `schemas.py` and are separate from database models, so a new column does not leak
into the API. Validate with field types and constraints (`Field(gt=0)`, `Literal`), not with checks in the route.
The `api-design` skill covers status codes, errors and compatibility.

## 8. Tests

Test services directly with fakes. Test routers through the app with the dependency replaced:

```python
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.orders.dependencies import get_order_service
from app.orders.service import OrderService
from tests.fakes import FakeOrderStore


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_order_service] = lambda: OrderService(FakeOrderStore())
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_unknown_order_is_404(client: TestClient) -> None:
    response = client.get("/orders/999")

    assert response.status_code == 404
```

`TestClient(app)` without a `with` block does not run the lifespan, so no real database is opened. Run the tests with
`uv run pytest`, and look at the OpenAPI page (`/docs`) in the preview after starting the server with `start_process`
and its port.

## Report

```
Endpoints: <method path - service call>
Logic: <which service and functions hold it>
Settings: <new variables, added to .env.example>
Tests: <service tests and router tests, with the command and result>
```
