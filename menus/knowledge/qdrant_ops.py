# menus/knowledge/qdrant_ops.py -- Qdrant low-level operations


def _ensure_collection(client, name: str, dim: int = 4096) -> None:
    created = False
    try:
        client.get_collection(name)
    except Exception:
        import requests as _req
        from config import QDRANT_URL
        payload = {
            "vectors":        {"dense":  {"size": dim, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
        }
        _req.put(f"{QDRANT_URL}/collections/{name}", json=payload, timeout=10)
        created = True

    if created and name.startswith("knowledge_"):
        try:
            from qdrant_client.models import PayloadSchemaType
            client.create_payload_index(name, "topic_slug", PayloadSchemaType.KEYWORD)
            client.create_payload_index(name, "category",   PayloadSchemaType.KEYWORD)
            client.create_payload_index(name, "evergreen",  PayloadSchemaType.BOOL)
        except Exception:
            pass  # Indexes are optional


def _fetch_with_vectors(qdrant_url: str, collection: str, point_id) -> dict | None:
    import requests as _req
    try:
        resp = _req.post(
            f"{qdrant_url}/collections/{collection}/points",
            json={"ids": [point_id], "with_payload": True, "with_vectors": True},
            timeout=10,
        )
        data = resp.json().get("result", [])
        return data[0] if data else None
    except Exception as e:
        print(f"  [fetch] Error: {e}")
        return None


def _upsert_point(qdrant_url: str, collection: str, point: dict) -> bool:
    import requests as _req
    try:
        resp = _req.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": [point]},
            timeout=30,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"  [upsert] Error: {e}")
        return False


def _delete_ids(qdrant_url: str, collection: str, ids: list) -> None:
    import requests as _req
    if not ids:
        return
    try:
        _req.post(
            f"{qdrant_url}/collections/{collection}/points/delete",
            json={"points": ids},
            timeout=10,
        )
    except Exception:
        pass


def _load_all_points(client, col: str) -> list:
    """Load all points from collection with pagination."""
    pts = []
    offset = None
    while True:
        result = client.scroll(
            collection_name=col, limit=200, offset=offset,
            with_payload=True, with_vectors=False,
        )
        batch, offset = result
        pts.extend(batch)
        if offset is None:
            break
    return pts
