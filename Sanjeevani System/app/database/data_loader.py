import json
import os
from app.database.mongo_client import get_db


def load_data():
    db = get_db()

    # Paths to JSON files
    base_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    products_file = os.path.join(base_dir, "producst.json")
    patients_file = os.path.join(base_dir, "paitenetid.json")

    # 1. Load products
    if os.path.exists(products_file):
        with open(products_file, "r", encoding="utf-8") as f:
            products_data = json.load(f)
            # data is inside 'data' key of the first list item
            if (
                isinstance(products_data, list)
                and len(products_data) > 0
                and "data" in products_data[0]
            ):
                raw_products = products_data[0]["data"]
                cleaned_products = []
                for p in raw_products:
                    cleaned_product = {
                        "product_id": p.get("product id"),
                        "product_name": p.get("product name"),
                        "pzn": p.get("pzn"),
                        "price_rec": p.get("price rec"),
                        "package_size": p.get("package size"),
                        "description": p.get(
                            "descriptions"
                        ),  # renamed descriptions to description or keep it descriptions? Let's use descriptions
                    }
                    if "descriptions" in p:
                        cleaned_product["descriptions"] = p["descriptions"]
                    cleaned_products.append(cleaned_product)

                if cleaned_products:
                    db.products.delete_many({})
                    db.products.insert_many(cleaned_products)
                    print(f"Inserted {len(cleaned_products)} products.")

    # 2. Load consumer orders
    if os.path.exists(patients_file):
        with open(patients_file, "r", encoding="utf-8") as f:
            patients_data = json.load(f)
            if (
                isinstance(patients_data, list)
                and len(patients_data) > 0
                and "data" in patients_data[0]
            ):
                raw_orders = patients_data[0]["data"]
                cleaned_orders = []
                for o in raw_orders:
                    cleaned_order = {
                        "patient_id": o.get("Patient ID"),
                        "patient_age": o.get("Patient Age"),
                        "patient_gender": o.get("Patient Gender"),
                        "purchase_date": o.get("Purchase Date"),
                        "product_name": o.get("Product Name"),
                        "quantity": o.get("Quantity"),
                        "total_price_eur": o.get("Total Price (EUR)"),
                        "dosage_frequency": o.get("Dosage Frequency"),
                        "prescription_required": o.get("Prescription Required"),
                    }
                    cleaned_orders.append(cleaned_order)

                if cleaned_orders:
                    db.consumer_orders.delete_many({})
                    db.consumer_orders.insert_many(cleaned_orders)
                    print(f"Inserted {len(cleaned_orders)} consumer orders.")


if __name__ == "__main__":
    load_data()
