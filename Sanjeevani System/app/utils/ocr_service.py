"""
OCR Service for Prescription Text Extraction
Uses OCR.space API to extract text from prescription images
"""

import requests
import logging
from typing import Optional, Dict, List
import re

logger = logging.getLogger(__name__)

# OCR.space API Configuration
OCR_API_KEY = "K87110974788957"
OCR_API_URL = "https://api.ocr.space/parse/image"


def extract_text_from_image(image_path: str) -> Optional[str]:
    """
    Extract text from prescription image using OCR.space API
    
    Args:
        image_path: Path to the prescription image file
        
    Returns:
        Extracted text or None if failed
    """
    try:
        with open(image_path, "rb") as f:
            response = requests.post(
                OCR_API_URL,
                files={"file": f},
                data={
                    "apikey": OCR_API_KEY,
                    "language": "eng",
                    "isOverlayRequired": False,
                    "detectOrientation": True,
                    "scale": True,
                    "OCREngine": 2  # Use OCR Engine 2 for better accuracy
                },
                timeout=30
            )
        
        result = response.json()
        
        if result.get("IsErroredOnProcessing"):
            error_msg = result.get("ErrorMessage", ["Unknown error"])[0]
            logger.error(f"OCR processing error: {error_msg}")
            return None
        
        if not result.get("ParsedResults"):
            logger.error("No parsed results from OCR")
            return None
        
        text = result["ParsedResults"][0]["ParsedText"]
        logger.info(f"OCR extracted text length: {len(text)} characters")
        return text
        
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        return None


def extract_medicines_from_text(text: str) -> List[str]:
    """
    Extract medicine names from OCR text using pattern matching
    
    Args:
        text: Raw OCR extracted text
        
    Returns:
        List of potential medicine names
    """
    if not text:
        return []
    
    medicines = []
    lines = text.split('\n')
    
    # Common medicine name patterns
    # Usually medicine names are:
    # - Capitalized
    # - Followed by dosage (mg, ml, etc.)
    # - May have brand names in parentheses
    
    medicine_pattern = re.compile(
        r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)*)\s*(?:\d+\s*(?:mg|ml|g|mcg|%)?)?',
        re.MULTILINE
    )
    
    for line in lines:
        # Skip very short lines or lines with only numbers
        if len(line.strip()) < 3 or line.strip().isdigit():
            continue
        
        # Look for medicine patterns
        matches = medicine_pattern.findall(line)
        for match in matches:
            # Filter out common non-medicine words
            if match.lower() not in ['the', 'and', 'for', 'with', 'date', 'name', 'age', 'sex']:
                medicines.append(match)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_medicines = []
    for med in medicines:
        if med.lower() not in seen:
            seen.add(med.lower())
            unique_medicines.append(med)
    
    logger.info(f"Extracted {len(unique_medicines)} potential medicines: {unique_medicines}")
    return unique_medicines


async def verify_prescription_with_llm(
    ocr_text: str,
    extracted_medicines: List[str],
    llm_client
) -> Dict:
    """
    Verify prescription authenticity and extract medicines using LLM
    
    Args:
        ocr_text: Raw OCR text
        extracted_medicines: Pre-extracted medicine names
        llm_client: Groq LLM client
        
    Returns:
        Dict with verification results and refined medicine list
    """
    try:
        prompt = f"""
You are a medical prescription verification AI. Analyze this prescription text and extract medicine information.

OCR Extracted Text:
{ocr_text}

Pre-identified medicines:
{', '.join(extracted_medicines) if extracted_medicines else 'None'}

Your task:
1. Verify if this looks like a valid prescription (has doctor info, patient info, medicines, dosage)
2. Extract all medicine names with their dosages
3. Identify if any medicines require special handling

Respond in JSON format:
{{
    "is_valid_prescription": true/false,
    "confidence": 0-100,
    "medicines": [
        {{"name": "Medicine Name", "dosage": "500mg", "frequency": "twice daily"}},
        ...
    ],
    "doctor_name": "Dr. Name or null",
    "warnings": ["any warnings or concerns"]
}}
"""
        
        response = llm_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are a medical prescription verification expert. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        import json
        result = json.loads(response.choices[0].message.content)
        logger.info(f"LLM verification result: {result}")
        return result
        
    except Exception as e:
        logger.error(f"LLM verification failed: {e}")
        return {
            "is_valid_prescription": False,
            "confidence": 0,
            "medicines": [],
            "warnings": [f"Verification failed: {str(e)}"]
        }
