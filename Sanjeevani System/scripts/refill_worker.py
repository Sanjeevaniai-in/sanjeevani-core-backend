import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure `app.*` imports work when script is executed directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.refill_outreach import RefillOutreachService

load_dotenv()


def run_refill_scanner() -> None:
    """
    Demo worker:
    - By default uses demo JSON and sends outreach to WhatsApp + app notification collection.
    - Set REFILL_USE_DEMO_DATA=false to scan live orders.
    """
    merchant_id = os.getenv("DEFAULT_PHARMACY_ID", "").strip() or os.getenv("DEFAULT_MERCHANT_ID", "").strip()
    if not merchant_id:
        print("Missing DEFAULT_PHARMACY_ID/DEFAULT_MERCHANT_ID in environment.")
        return

    use_demo = os.getenv("REFILL_USE_DEMO_DATA", "true").strip().lower() in ("1", "true", "yes", "y")
    demo_file = os.getenv("REFILL_DEMO_FILE_PATH", "").strip() or None

    service = RefillOutreachService()
    if use_demo:
        result = service.run_demo_outreach(merchant_id=merchant_id, demo_file_path=demo_file)
    else:
        result = service.run_live_outreach(merchant_id=merchant_id, reminder_days=[10, 28])

    print("Refill worker completed:")
    print(result)


if __name__ == "__main__":
    run_refill_scanner()
