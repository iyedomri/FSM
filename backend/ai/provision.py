"""Explicit, one-time creation of a dedicated 1536-dimension Pinecone index."""
import os
import time
from pinecone import Pinecone, ServerlessSpec
from config import settings

if __name__ == "__main__":
    client = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())
    name = os.environ.get("PINECONE_INDEX_NAME", "synapse")
    if name not in client.list_indexes().names():
        client.create_index(name=name, dimension=1536, metric="cosine",
                            spec=ServerlessSpec(cloud="aws", region=os.environ.get("PINECONE_REGION", "us-east-1")))
    for _ in range(120):
        info = client.describe_index(name)
        if info.status["ready"]:
            if info.dimension != 1536:
                raise SystemExit("Index dimension must be 1536")
            print("PINECONE_INDEX_HOST=" + info.host)
            break
        time.sleep(2)
    else:
        raise SystemExit("Index provisioning timed out")
