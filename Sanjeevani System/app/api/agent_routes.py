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
    """Process an order submitted by the Assistant through the AI agent pipeline.

    Endpoint for the Assistant to call with extracted order items. Runs the
    4-Agent pipeline (extraction, validation, safety, and fulfillment) and
    returns a safety-validated response.

    Args:
        request: Order details including the requesting user's phone number,
            the target merchant/pharmacy ID, and the list of extracted items
            (name and quantity) to process.

    Returns:
        dict: The safety-validated result produced by the agent orchestrator,
            as returned by ``AgentOrchestrator.process_order``.

    Raises:
        HTTPException: 500 if the agent pipeline fails for any reason (e.g.
            orchestration error, downstream service failure).
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