from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from ..modules.agent_orchestrator import AgentOrchestrator
from ..utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/agent", tags=["AI Agents"])

class ExtractedItem(BaseModel):
    name: str
    quantity: int = 1

class AgentOrderRequest(BaseModel):
    user_phone: str
    merchant_id: str
    items: List[ExtractedItem]

@router.post("/process-order")
async def process_order_with_agents(request: AgentOrderRequest):
    """
    Endpoint for the Assistant to call with extracted order items.
    Runs the 4-Agent pipeline and returns a safety-validated response.
    """
    logger.info(f"AI Agent: Processing order for {request.user_phone} at pharmacy {request.merchant_id}")
    
    try:
        orchestrator = AgentOrchestrator()
        result = await orchestrator.process_order(
            user_phone=request.user_phone,
            merchant_id=request.merchant_id,
            extracted_items=[item.model_dump() for item in request.items]
        )
        return result
    except Exception as e:
        logger.error(f"AI Agent: Failed to process order. Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI Agent Error: {str(e)}")
