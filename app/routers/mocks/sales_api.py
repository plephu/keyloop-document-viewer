"""Mocked external Sales System API.

Simulates a legacy dealership sales system with its OWN response format
(different field names from the Service System) — this forces the
aggregator to do real normalisation work, as it would in production.

Special test VINs (for demos and failure testing):
- VIN ending in "E" (and not in the dataset) -> HTTP 500 (source outage)
- VIN ending in "T"                          -> sleeps 5s (timeout)
"""
import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/mock/sales-system", tags=["Mock: Sales System API"])

_SALES_DOCS = {
    "1HGBH41JXMN109186": [
        {
            "docId": "S-2024-0001",
            "docName": "Vehicle Purchase Agreement",
            "category": "CONTRACT",
            "dateCreated": "2024-03-15",
            "downloadLink": "https://sales.dealer.example/docs/S-2024-0001.pdf",
        },
        {
            "docId": "S-2024-0002",
            "docName": "Finance Application",
            "category": "FINANCE",
            "dateCreated": "2024-03-14",
            "downloadLink": "https://sales.dealer.example/docs/S-2024-0002.pdf",
        },
        {
            "docId": "S-2024-0003",
            "docName": "Trade-in Valuation Report",
            "category": "VALUATION",
            "dateCreated": "2024-03-10",
            "downloadLink": "https://sales.dealer.example/docs/S-2024-0003.pdf",
        },
    ],
    "5YJSA1E26MF123456": [
        {
            "docId": "S-2025-0107",
            "docName": "EV Purchase Contract",
            "category": "CONTRACT",
            "dateCreated": "2025-01-20",
            "downloadLink": "https://sales.dealer.example/docs/S-2025-0107.pdf",
        },
    ],
}


@router.get("/documents/{vin}")
async def get_sales_documents(vin: str):
    vin = vin.upper()
    if vin.endswith("E") and vin not in _SALES_DOCS:
        raise HTTPException(status_code=500, detail="Sales system internal error")
    if vin.endswith("T"):
        await asyncio.sleep(5)  # longer than the aggregator's timeout
    return {"vehicleVin": vin, "documents": _SALES_DOCS.get(vin, [])}
