
import pymongo

def main():
    uri = "mongodb+srv://samaypowade9:samay2005@cluster0.jvwb3d8.mongodb.net/?appName=Cluster0"
    client = pymongo.MongoClient(uri)
    
    # List databases
    print("Databases:", client.list_database_names())
    
    # List collections in sanjeevani_rx_db
    db = client["sanjeevani_rx_db"]
    print("Collections in sanjeevani_rx_db:", db.list_collection_names())
    
    # Check a few collections
    for coll in ["users", "pharmacies", "consumer_orders", "products"]:
        count = db[coll].count_documents({})
        print(f"Collection '{coll}' count: {count}")
        if count > 0:
            doc = db[coll].find_one()
            print(f"Sample from '{coll}':", doc)

if __name__ == "__main__":
    main()
