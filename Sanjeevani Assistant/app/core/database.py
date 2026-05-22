from motor.motor_asyncio import AsyncIOMotorClient
from .config import DEFAULT_MERCHANT_ID, DEFAULT_PHARMACY_ID, MONGODB_URL, POSTGRES_DSN, SUPABASE_DB_URL, MONGO_URI
from .logger import logger
from .pg_document_db import AsyncDocumentDatabase, PostgresDocumentStore

store = None
db = None
users_collection = None
orders_collection = None
addresses_collection = None
conversations_collection = None
channel_bindings_collection = None


def _resolve_dsn() -> str:
    return MONGO_URI or SUPABASE_DB_URL or POSTGRES_DSN or MONGODB_URL


def init_db():
    global store, db, users_collection, orders_collection, addresses_collection, conversations_collection, channel_bindings_collection
    dsn = _resolve_dsn()
    try:
        if dsn.startswith("mongodb"):
            store = AsyncIOMotorClient(dsn)
            try:
                db = store.get_default_database()
            except Exception:
                db = store["sanjeevani_assistant"]
            logger.info("Connected to Native MongoDB")
        else:
            store = PostgresDocumentStore(dsn)
            store.ensure_schema()
            db = AsyncDocumentDatabase(store)
            logger.info("Connected to Postgres document store")
            
        users_collection = db["users"]
        orders_collection = db["consumer_orders"]
        addresses_collection = db["addresses"]
        conversations_collection = db["conversations"]
        channel_bindings_collection = db["channel_bindings"]
    except Exception as exc:
        logger.error(f"Database init failed: {exc}")
        store = None
        db = None
        users_collection = None
        orders_collection = None
        addresses_collection = None
        conversations_collection = None
        channel_bindings_collection = None


init_db()

