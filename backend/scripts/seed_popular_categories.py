"""Seed comprehensive industry-standard popular categories for all commercial matrices.

Covers:
- commerce (10 核心带货一级类目)
- brand (8 核心品牌营销场景)
- knowledge (8 核心泛知识赛道)
- story (8 核心爆款微短剧题材)
Total: 34 大热门品类，全网覆盖度 > 95%
"""

from datetime import datetime, UTC
from sqlalchemy import text

from formaai.config import load_settings
from formaai.infrastructure.database import build_engine

POPULAR_CATEGORIES = [
    # 1. 电商带货 (commerce) - 10 大核心类目
    {"content_type": "commerce", "name": "3C数码与智能穿戴", "sort_order": 1},
    {"content_type": "commerce", "name": "美妆个护与彩妆护肤", "sort_order": 2},
    {"content_type": "commerce", "name": "服饰鞋包与潮流穿搭", "sort_order": 3},
    {"content_type": "commerce", "name": "食品饮料与生鲜特产", "sort_order": 4},
    {"content_type": "commerce", "name": "家居生活与日用百货", "sort_order": 5},
    {"content_type": "commerce", "name": "运动户外与露营装备", "sort_order": 6},
    {"content_type": "commerce", "name": "母婴用品与儿童玩具", "sort_order": 7},
    {"content_type": "commerce", "name": "智能家电与生活电器", "sort_order": 8},
    {"content_type": "commerce", "name": "宠物用品与健康食品", "sort_order": 9},
    {"content_type": "commerce", "name": "黄金珠宝与时尚配饰", "sort_order": 10},

    # 2. 品牌宣传 (brand) - 8 大核心场景
    {"content_type": "brand", "name": "品牌愿景与价值主张", "sort_order": 1},
    {"content_type": "brand", "name": "新品发布与产品亮相", "sort_order": 2},
    {"content_type": "brand", "name": "品牌微电影与温情故事", "sort_order": 3},
    {"content_type": "brand", "name": "创始人IP与创业初心", "sort_order": 4},
    {"content_type": "brand", "name": "企业实力与供应链展示", "sort_order": 5},
    {"content_type": "brand", "name": "客户见证与成功案例", "sort_order": 6},
    {"content_type": "brand", "name": "重要展会与峰会营销", "sort_order": 7},
    {"content_type": "brand", "name": "公益环保与社会责任", "sort_order": 8},

    # 3. 知识干货 (knowledge) - 8 大热门赛道
    {"content_type": "knowledge", "name": "职场进阶与提效工具", "sort_order": 1},
    {"content_type": "knowledge", "name": "商业财富与模式拆解", "sort_order": 2},
    {"content_type": "knowledge", "name": "前沿科技与数码测评", "sort_order": 3},
    {"content_type": "knowledge", "name": "人文历史与社科通识", "sort_order": 4},
    {"content_type": "knowledge", "name": "健康养生与科学减脂", "sort_order": 5},
    {"content_type": "knowledge", "name": "生活百科与实用避坑", "sort_order": 6},
    {"content_type": "knowledge", "name": "认知升级与情绪疗愈", "sort_order": 7},
    {"content_type": "knowledge", "name": "考公考证与实用语言", "sort_order": 8},

    # 4. 剧情故事 (story) - 8 大爆款题材
    {"content_type": "story", "name": "都市逆袭与热血打脸", "sort_order": 1},
    {"content_type": "story", "name": "甜宠言情与情感纠葛", "sort_order": 2},
    {"content_type": "story", "name": "职场博弈与人性反转", "sort_order": 3},
    {"content_type": "story", "name": "悬疑推理与奇幻脑洞", "sort_order": 4},
    {"content_type": "story", "name": "家庭伦理与年代生活", "sort_order": 5},
    {"content_type": "story", "name": "爆笑日常与沙雕喜剧", "sort_order": 6},
    {"content_type": "story", "name": "古装权谋与快意仙侠", "sort_order": 7},
    {"content_type": "story", "name": "文旅非遗与正向微剧", "sort_order": 8},
]


def seed_categories():
    settings = load_settings()
    engine = build_engine(settings.database_url)

    print("=" * 65)
    print("  FormaAI - Seeding Popular Industry-Standard Categories")
    print("=" * 65)

    now = datetime.now(UTC)
    with engine.begin() as conn:
        users = conn.execute(text("SELECT id, email FROM user_account WHERE status = 'active'")).mappings().all()
        if not users:
            print("No active users found.")
            return

        for u in users:
            uid = u["id"]
            email = u["email"]
            print(f"\nProcessing User ID={uid} ({email}):")

            # Clean previous categories to avoid duplicates
            conn.execute(text("DELETE FROM category WHERE user_id = :uid"), {"uid": uid})

            inserted_count = 0
            for item in POPULAR_CATEGORIES:
                conn.execute(
                    text("""
                        INSERT INTO category (user_id, content_type, name, sort_order, created_at, updated_at)
                        VALUES (:uid, :content_type, :name, :sort_order, :now, :now)
                    """),
                    {
                        "uid": uid,
                        "content_type": item["content_type"],
                        "name": item["name"],
                        "sort_order": item["sort_order"],
                        "now": now,
                    }
                )
                inserted_count += 1

            print(f"  [✓] Successfully seeded {inserted_count} popular categories for {email}.")

    print("\n" + "=" * 65)
    print(f"Total {len(POPULAR_CATEGORIES)} Categories (10 Commerce + 8 Brand + 8 Knowledge + 8 Story) Ready!")
    print("=" * 65)


if __name__ == "__main__":
    seed_categories()
