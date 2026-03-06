from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import threading

from prometheus_client import start_http_server
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # tracing auto-instrumentation

app = FastAPI(title="Inventory Service", version="1.0")

lock = threading.Lock()
inventory: Dict[str, int] = {
    "p101": 20,
    "p102": 5,
    "p103": 0
}

resource = Resource(attributes={SERVICE_NAME: "inventory-service"})
reader = PrometheusMetricReader()
provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(provider)
meter = metrics.get_meter("inventory-meter")

start_http_server(port=9464, addr="0.0.0.0")  # exposes /metrics

# Custom metrics
stock_check_counter = meter.create_counter(
    name="inventory_stock_checks_total",
    description="Total stock check calls",
    unit="1",
)
reserve_success_counter = meter.create_counter(
    name="inventory_reserve_success_total",
    description="Total successful reservations",
    unit="1",
)
reserve_failure_counter = meter.create_counter(
    name="inventory_reserve_failure_total",
    description="Total failed reservations",
    unit="1",
)

FastAPIInstrumentor.instrument_app(app) 
class ReserveRequest(BaseModel):
    product_id: str
    quantity: int

@app.get("/inventory/{product_id}")
def get_stock(product_id: str):
    stock_check_counter.add(1, {"endpoint": "/inventory/{product_id}"})
    with lock:
        qty = inventory.get(product_id, 0)
    return {"product_id": product_id, "available": qty > 0, "quantity": qty}

@app.post("/inventory/reserve")
def reserve_stock(req: ReserveRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    stock_check_counter.add(1, {"endpoint": "/inventory/reserve"})

    with lock:
        available_qty = inventory.get(req.product_id, 0)
        if available_qty >= req.quantity:
            inventory[req.product_id] = available_qty - req.quantity
            reserve_success_counter.add(1, {"product_id": req.product_id})
            return {
                "reserved": True,
                "product_id": req.product_id,
                "reserved_quantity": req.quantity,
                "remaining": inventory[req.product_id],
            }

    reserve_failure_counter.add(1, {"product_id": req.product_id})
    raise HTTPException(status_code=409, detail="Out of stock")