from app.config import get_settings
from pymongo import MongoClient

def main():
    settings = get_settings()
    client = MongoClient(settings.mongodb_uri)
    db = client[settings.mongodb_db_name]
    res = db.insights.delete_many({"type": "error"})
    print(res.deleted_count)

main()
