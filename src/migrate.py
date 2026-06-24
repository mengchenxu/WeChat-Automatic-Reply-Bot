"""数据迁移 — 旧 JSON → 新 store.json"""
import json
import logging
import os
import shutil
import time

from src.store import Store, FactEntry

logger = logging.getLogger(__name__)


def migrate(data_dir: str = "data", backup: bool = True) -> Store:
    """
    从旧的 users.json / group_memories.json / group_contexts.json
    迁移到 data/store.json。返回新 Store。
    """
    store = Store()

    # 1. 迁移 users.json → store.people
    users_path = os.path.join(data_dir, "users.json")
    if os.path.exists(users_path):
        with open(users_path, "r", encoding="utf-8") as f:
            users = json.load(f)
        for wxid, d in users.items():
            if not wxid or wxid.startswith("__placeholder__"):
                continue
            # 取人类可读名作为 mention_name
            raw_name = d.get("preferred_name", "") or ""
            mention = raw_name if raw_name and not raw_name.startswith("wxid_") else ""

            p = store.get_or_create_person(wxid, mention)

            # 外号
            for alias in d.get("aliases", []):
                if alias and alias not in p.aliases:
                    p.aliases.append(alias)

            # 事实（支持新旧格式）
            facts_raw = d.get("known_facts", {})
            for k, v in facts_raw.items():
                if isinstance(v, dict) and "value" in v:
                    p.add_fact(k, v.get("value", ""), v.get("source", "legacy"),
                              v.get("confidence", 0.5))
                else:
                    p.add_fact(k, str(v), source="legacy", confidence=0.5)

            # 口头禅
            for cp in d.get("catchphrases", []):
                if cp and cp not in p.catchphrases:
                    p.catchphrases.append(cp)

            p.first_seen = d.get("first_seen", 0.0)
            p.last_seen = d.get("last_seen", 0.0)
        logger.info("Migrated %d users from users.json", len(users))

    # 2. 迁移 group_memories.json → store.groups[room].memories
    mem_path = os.path.join(data_dir, "group_memories.json")
    if os.path.exists(mem_path):
        with open(mem_path, "r", encoding="utf-8") as f:
            memories = json.load(f)
        for room_id, mems in memories.items():
            if not room_id:
                continue
            g = store.get_group(room_id)
            for m in mems:
                g.add_memory(
                    text=m.get("content", ""),
                    keywords=m.get("keywords", []),
                    category=m.get("category", "fact"),
                    importance=m.get("importance", 3),
                )
        logger.info("Migrated memories for %d groups", len(memories))

    # 3. 迁移 group_contexts.json → store.groups[room].context
    ctx_path = os.path.join(data_dir, "group_contexts.json")
    if os.path.exists(ctx_path):
        with open(ctx_path, "r", encoding="utf-8") as f:
            contexts = json.load(f)
        for room_id, d in contexts.items():
            if not room_id:
                continue
            g = store.get_group(room_id)
            g.context = d.get("context", "") if isinstance(d, dict) else str(d)
            if isinstance(d, dict):
                g.msg_count = d.get("message_count", 0)
        logger.info("Migrated contexts for %d groups", len(contexts))

    # 4. 写入 store.json
    store.save(os.path.join(data_dir, "store.json"))
    store._meta["last_sync"] = time.time()

    # 5. 备份旧文件
    if backup:
        for name in ["users.json", "group_memories.json", "group_contexts.json"]:
            src = os.path.join(data_dir, name)
            if os.path.exists(src):
                dst = src + ".bak"
                shutil.move(src, dst)
                logger.info("Backed up: %s → %s", name, name + ".bak")

    store.save(os.path.join(data_dir, "store.json"))
    return store


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    store = migrate()
    print(f"Migration complete: {len(store._people)} people, {len(store._groups)} groups")
