import requests
from html.parser import HTMLParser
import os
import logging
from pymongo import MongoClient, UpdateOne
import time
import random
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading
import json

# Import secure configuration
from secure_config import get_config

# =========================
# CONFIG
# =========================
config = get_config()

# Validate configuration on startup
if not config.validate():
    raise RuntimeError("Configuration validation failed. Check your .env file.")

MAX_WORKERS = 8              
MAX_INFLIGHT = 200          
BATCH_SIZE = 500
MAX_RETRY = 3

logging.basicConfig(level=logging.INFO)
logging.info("✅ Secure configuration loaded")

# =========================
# THREAD LOCAL SESSION
# =========================
thread_local = threading.local()

def get_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

# =========================
# MONGO
# =========================
def get_mongo_client():
    """
    Create MongoDB client with credentials from secure config
    Credentials are never stored - only used to build URI once
    """
    try:
        mongo_uri = config.get_mongo_uri()
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        # Verify connection
        client.server_info()
        logging.info("Connected to MongoDB")
        return client
    except Exception as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        raise

def load_product_data(db):
    """
    Load product data from multiple event collections with proper filtering:
    - From 6 collections: extract product_id (or viewing_product_id if missing) and current_url
    - From product_view_all_recommend_clicked: extract viewing_product_id and referrer_url
    - Deduplicate by product_id, keeping one active record per product
    """
    
    # Collections that use product_id/viewing_product_id and current_url
    product_collections = [
        "view_product_detail",
        "select_product_option",
        "select_product_option_quality",
        "add_to_cart_action",
        "product_detail_recommendation_visible",
        "product_detail_recommendation_noticed"
    ]
    
    # Collection that uses viewing_product_id and referrer_url
    recommend_collection = "product_view_all_recommend_clicked"
    
    product_data = {}  # Dictionary to store product_id -> {product_id, url}
    
    try:
        # ==== FILTER 1: First 6 collections ====
        pipeline = [
            {
                "$match": {
                    "collection": {"$in": product_collections},
                    "$or": [
                        {"product_id": {"$exists": True, "$ne": None, "$ne": ""}},
                        {"viewing_product_id": {"$exists": True, "$ne": None, "$ne": ""}}
                    ]
                }
            },
            {
                "$project": {
                    "product_id": {
                        "$cond": [
                            {"$and": [
                                {"$ne": ["$product_id", None]},
                                {"$ne": ["$product_id", ""]}
                            ]},
                            "$product_id",
                            "$viewing_product_id"
                        ]
                    },
                    "current_url": 1,
                    "timestamp": 1
                }
            },
            {
                "$match": {
                    "product_id": {"$exists": True, "$ne": None, "$ne": ""}
                }
            }
        ]
        
        docs = list(db.summary.aggregate(pipeline, allowDiskUse=True))
        logging.info(f"✅ Loaded {len(docs)} records from 6 product collections")
        
        for doc in docs:
            product_id = doc["product_id"]
            url = doc.get("current_url")
            # Keep the record with the latest timestamp for each product_id
            if product_id not in product_data or doc.get("timestamp", 0) > product_data[product_id].get("timestamp", 0):
                product_data[product_id] = {
                    "product_id": product_id,
                    "url": url,
                    "timestamp": doc.get("timestamp", 0)
                }
        
        # ==== FILTER 2: product_view_all_recommend_clicked collection ====
        pipeline2 = [
            {
                "$match": {
                    "collection": recommend_collection,
                    "viewing_product_id": {"$exists": True, "$ne": None, "$ne": ""}
                }
            },
            {
                "$project": {
                    "product_id": "$viewing_product_id",
                    "referrer_url": 1,
                    "timestamp": 1
                }
            }
        ]
        
        docs2 = list(db.summary.aggregate(pipeline2, allowDiskUse=True))
        logging.info(f"✅ Loaded {len(docs2)} records from product_view_all_recommend_clicked")
        
        for doc in docs2:
            product_id = doc["product_id"]
            url = doc.get("referrer_url")
            # Keep the record with the latest timestamp for each product_id
            if product_id not in product_data or doc.get("timestamp", 0) > product_data[product_id].get("timestamp", 0):
                product_data[product_id] = {
                    "product_id": product_id,
                    "url": url,
                    "timestamp": doc.get("timestamp", 0)
                }
        
        # Return deduplicated list without timestamp
        result = [
            {"product_id": pid, "url": data["url"]} 
            for pid, data in product_data.items()
        ]
        
        logging.info(f"✅ Total unique products after deduplication: {len(result)}")
        return result
        
    except Exception as e:
        logging.error(f"❌ Failed to load product data: {e}")
        return []

# =========================
# PARSER
# =========================
class ReactDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.react_data = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.in_script = True

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and "var react_data =" in data:
            start = data.find("{")
            end = data.rfind("}") + 1
            if start != -1 and end != -1:
                try:
                    self.react_data = json.loads(data[start:end])
                except json.JSONDecodeError as e:
                    logging.debug(f"Failed to parse react_data JSON: {e}")

def extract_react_data(html):
    parser = ReactDataParser()
    parser.feed(html)
    return parser.react_data

# =========================
# CRAWL
# =========================
def crawl_one(info):
    product_id = info["product_id"]
    url = f"https://www.glamira.com/catalog/product/view/id/{product_id}"
    session = get_session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            res = session.get(url, headers=headers, timeout=10)
            logging.info(f"FETCH {product_id} -> {res.status_code} size={len(res.text)}")

            if random.random() < 0.01:
                logging.info(f"[{product_id}] status={res.status_code}, len={len(res.text)}")

            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}")

            react_data = extract_react_data(res.text)
            if not react_data:
                logging.warning(f"NO PARSED DATA {product_id}")
                raise Exception("No react_data")

            time.sleep(random.uniform(0.5, 1.5))

            return {
                "product_id": product_id,
                "name": react_data.get("name"),
                "sku": react_data.get("sku"),
                "price": react_data.get("price"),
                "min_price": react_data.get("min_price"),
                "max_price": react_data.get("max_price"),
                "collection": react_data.get("collection"),
                "category_name": react_data.get("category_name"),
                "gender": react_data.get("gender"),
                "quick_options": react_data.get("quick_options", [])
            }

        except Exception as e:
            if attempt == MAX_RETRY:
                logging.warning(f"[{product_id}] FAIL: {e}")
                return None

            time.sleep(2 ** attempt + random.uniform(0, 1))

# =========================
# MONGO WRITE
# =========================
def write_mongo_batch(db, rows):
    if not rows:
        return

    ops = [
        UpdateOne(
            {"product_id": r["product_id"]},
            {"$set": r},
            upsert=True
        )
        for r in rows
    ]

    try:
        db.product_info.bulk_write(ops, ordered=False)
    except Exception as e:
        logging.error(f"Mongo write error: {e}")

# =========================
# MAIN PIPELINE
# =========================
def crawl_product_information(db, infos):
    batch = []
    total = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = set()

        for i, info in enumerate(infos):
            futures.add(executor.submit(crawl_one, info))

            if len(futures) >= MAX_INFLIGHT:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)

                for future in done:
                    try:
                        r = future.result()
                        if r:
                            batch.append(r)
                    except Exception as e:
                        logging.error(f"Thread error: {e}")

            if len(batch) >= BATCH_SIZE:
                write_mongo_batch(db, batch)
                total += len(batch)
                logging.info(f"Inserted {total}")
                batch = []

            if i % 1000 == 0:
                logging.info(f"Processed {i}")

        # flush remaining
        for future in futures:
            try:
                r = future.result()
                if r:
                    batch.append(r)
            except Exception as e:
                logging.error(f"Error retrieving future result: {e}")

    if batch:
        write_mongo_batch(db, batch)
        total += len(batch)

    logging.info(f"Total inserted: {total}")

# =========================
# MAIN
# =========================
def main():
    try:
        # Get secure MongoDB connection
        client = get_mongo_client()
        
        # Get database name from secure config
        db_config = config.get_db_config()
        db = client[db_config["db_name"]]

        logging.info("Start filtering and crawling product information...")
        
        # Load deduplicated product data with URLs
        product_data = load_product_data(db)   # Returns list of {product_id, url}
        
        if not product_data:
            logging.warning("No product data found to crawl")
            return
        
        logging.info(f"Starting crawl of {len(product_data)} unique products...")
        crawl_product_information(db, product_data)

        db.product_info.create_index([("product_id", 1)], unique=True)

        logging.info("✅ DONE")
        
    except Exception as e:
        logging.error(f"Error in main: {e}")
        raise
    finally:
        if 'client' in locals():
            client.close()
            logging.info("MongoDB connection closed")

if __name__ == "__main__":
    main()