"""Seed a clean, realistic production drill scenario.

Inserts standard enterprise materials and an initial task for the primary user
so that an end-to-end rehearsal can be conducted immediately.
"""

from datetime import datetime, UTC
import json
from pathlib import Path
from sqlalchemy import text

from formaai.config import load_settings
from formaai.infrastructure.database import build_engine
from formaai.infrastructure.storage import LocalStorageService


def seed_drill_scenario():
    settings = load_settings()
    engine = build_engine(settings.database_url)
    storage = LocalStorageService(settings.storage_dir)

    print("=" * 60)
    print("FormaAI - Injecting Production Drill Benchmark Scenario")
    print("=" * 60)

    with engine.begin() as conn:
        # Find user
        user = conn.execute(
            text("SELECT id, email FROM user_account WHERE email = 'binary.soul.dev@outlook.com'")
        ).mappings().first()
        if not user:
            print("Primary user binary.soul.dev@outlook.com not found!")
            return

        user_id = user["id"]

        # Ensure commerce category exists
        cat = conn.execute(
            text("SELECT id FROM category WHERE user_id = :uid AND content_type = 'commerce' LIMIT 1"),
            {"uid": user_id}
        ).mappings().first()

        cat_id = cat["id"] if cat else 1

        # 1. Add 2 Standard Enterprise Materials
        now = datetime.now(UTC)
        mat1_payload = {
            "title": "2026旗舰超轻量碳板跑鞋产品企划案",
            "material_type": "text",
            "content": "核心技术卖点：全掌超临界发泡中底，能量回馈率高达88%；单只42码仅重165克；航空级抗水微孔鞋面，雨雪全天候干爽通勤与专业路跑兼备。",
        }
        raw_key1, _ = storage.save_file(
            file_data=json.dumps(mat1_payload, ensure_ascii=False).encode("utf-8"),
            filename="product_brief.json",
            content_type="application/json",
            user_id=user_id,
        )
        conn.execute(
            text("""
                INSERT INTO material (category_id, user_id, title, material_type, storage_key, version, created_at, updated_at)
                VALUES (:cid, :uid, :title, :mtype, :skey, 0, :now, :now)
            """),
            {
                "cid": cat_id,
                "uid": user_id,
                "title": mat1_payload["title"],
                "mtype": mat1_payload["material_type"],
                "skey": raw_key1,
                "now": now,
            }
        )

        mat2_payload = {
            "title": "目标受众痛点与商业转化话术指南",
            "material_type": "text",
            "content": "目标客群：25-40岁都市白领、晨跑跑者、轻户外通勤人群。核心抗拒点：传统雨季跑鞋笨重闷脚；破局切入点：3秒淋水实测与上称实拍反转对比。",
        }
        raw_key2, _ = storage.save_file(
            file_data=json.dumps(mat2_payload, ensure_ascii=False).encode("utf-8"),
            filename="market_insights.json",
            content_type="application/json",
            user_id=user_id,
        )
        conn.execute(
            text("""
                INSERT INTO material (category_id, user_id, title, material_type, storage_key, version, created_at, updated_at)
                VALUES (:cid, :uid, :title, :mtype, :skey, 0, :now, :now)
            """),
            {
                "cid": cat_id,
                "uid": user_id,
                "title": mat2_payload["title"],
                "mtype": mat2_payload["material_type"],
                "skey": raw_key2,
                "now": now,
            }
        )

        print("[✓] 2 Standard Enterprise Materials injected into Library.")

        # 2. Add 1 Initial Task in 'created' stage
        task_input = {
            "theme": "2026 旗舰级超轻碳板防水跑鞋",
            "target_market": "都市白领 · 晨跑夜跑跑者 · 轻户外通勤人群",
            "allow_ai_expansion": True,
        }
        input_key, _ = storage.save_file(
            file_data=json.dumps(task_input, ensure_ascii=False).encode("utf-8"),
            filename="task_init.json",
            content_type="application/json",
            user_id=user_id,
        )
        conn.execute(
            text("""
                INSERT INTO video_task (category_id, user_id, name, content_type, stage, status, input_key, checkpoint_key, version, created_at, updated_at)
                VALUES (:cid, :uid, '2026轻量跑鞋15秒信息流高转化视频', 'commerce', 'created', 'pending', :ikey, NULL, 0, :now, :now)
            """),
            {
                "cid": cat_id,
                "uid": user_id,
                "ikey": input_key,
                "now": now,
            }
        )
        print("[✓] 1 Benchmark Drill Task initialized at Stage 1 (created).")

    print("=" * 60)
    print("Drill Scenario is ready! You can now visit http://localhost:5173 to start rehearsal.")
    print("=" * 60)


if __name__ == "__main__":
    seed_drill_scenario()
