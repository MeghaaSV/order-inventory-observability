from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import uuid
import httpx

# ---- OpenTelemetry (metrics + traces) ----
from prometheus_client import start_http_server
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # tracing auto-instrumentation

app = FastAPI(title="Order Service", version="1.0")

INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8001")

# ---------- OpenTelemetry Prometheus Exporter ----------
# OTel Prometheus exporter usage shown here. [2](https://opentelemetry-python.readthedocs.io/en/latest/exporter/prometheus/prometheus.html)
resource = Resource(attributes={SERVICE_NAME: "order-service"})
reader = PrometheusMetricReader()
provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(provider)
meter = metrics.get_meter("order-meter")

start_http_server(port=9465, addr="0.0.0.0")  # exposes /metrics

# Custom metrics
order_requests_counter = meter.create_counter(
    name="order_requests_total",
    description="Total order requests",
    unit="1",
)
order_success_counter = meter.create_counter(
    name="order_success_total",
    description="Total successful orders",
    unit="1",
)
order_failure_counter = meter.create_counter(
    name="order_failure_total",
    description="Total failed orders",
    unit="1",
)

FastAPIInstrumentor.instrument_app(app)  # FastAPI instrumentation reference [3](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)

# Local memory for orders (simple demo)
orders = {}

class OrderRequest(BaseModel):
    product_id: str
    quantity: int

@app.post("/order")
async def place_order(req: OrderRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    order_requests_counter.add(1, {"endpoint": "/order"})
    reserve_payload = {"product_id": req.product_id, "quantity": req.quantity}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(f"{INVENTORY_URL}/inventory/reserve", json=reserve_payload)

        if resp.status_code == 200:
            order_id = str(uuid.uuid4())
            orders[order_id] = {
                "order_id": order_id,
                "product_id": req.product_id,
                "quantity": req.quantity,
                "status": "CONFIRMED",
            }
            order_success_counter.add(1, {"product_id": req.product_id})
            return orders[order_id]

        if resp.status_code == 409:
            order_failure_counter.add(1, {"reason": "out_of_stock"})
            raise HTTPException(status_code=409, detail="Out of stock")

        order_failure_counter.add(1, {"reason": f"inventory_{resp.status_code}"})
        raise HTTPException(status_code=502, detail="Inventory error")

    except httpx.RequestError:
        order_failure_counter.add(1, {"reason": "inventory_unreachable"})
        raise HTTPException(status_code=503, detail="Inventory service unavailable")

@app.get("/order/{order_id}")
def get_order(order_id: str):
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")
    return orders[order_id]