"""Mocked external Service System API.

Simulates a workshop/service management system. Deliberately uses a
DIFFERENT response shape from the Sales System, so the aggregator must
normalise both into one canonical Document model.

Special test VINs:
- VIN ending in "F" (and not in the dataset) -> HTTP 503 (source outage)
- VIN ending in "T"                          -> sleeps 5s (timeout)
"""
import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/mock/service-system", tags=["Mock: Service System API"])

_SERVICE_DOCS = {
    "1HGBH41JXMN109186": [
        {
            "id": 9001,
            "title": "60,000 km Major Service Invoice",
            "type": "invoice",
            "created": "2025-06-02T09:30:00Z",
            "file_url": "https://service.dealer.example/files/9001.pdf",
        },
        {
            "id": 9002,
            "title": "Brake Pad Replacement Work Order",
            "type": "work_order",
            "created": "2025-06-01T14:00:00Z",
            "file_url": "https://service.dealer.example/files/9002.pdf",
        },
    ],
    "5YJSA1E26MF123456": [
        {
            "id": 9101,
            "title": "Battery Health Inspection Report",
            "type": "inspection",
            "created": "2025-05-11T10:15:00Z",
            "file_url": "https://service.dealer.example/files/9101.pdf",
        },
        {
            "id": 9102,
            "title": "Software Update Confirmation",
            "type": "service_record",
            "created": "2025-04-22T16:45:00Z",
            "file_url": "https://service.dealer.example/files/9102.pdf",
        },
    ],
}


@router.get("/records/{vin}/files")
async def get_service_documents(vin: str):
    vin = vin.upper()
    if vin.endswith("F") and vin not in _SERVICE_DOCS:
        raise HTTPException(status_code=503, detail="Service system unavailable")
    if vin.endswith("T"):
        await asyncio.sleep(5)
    return {"vin": vin, "files": _SERVICE_DOCS.get(vin, [])}
