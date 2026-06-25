"""数据迁移 — 旧 JSON → 新 store.json（委托给 Store.migrate_from_old_files）"""
import logging

from src.store import Store

logger = logging.getLogger(__name__)


def migrate(data_dir: str = "data") -> Store:
    """从旧的 users.json / group_memories.json 迁移到 store.json。幂等。"""
    import os
    store_path = os.path.join(data_dir, "store.json")
    return Store.migrate_from_old_files(store_path, data_dir=data_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    store = migrate()
    print(f"Migration complete: {len(store._people)} people, {len(store._groups)} groups")
