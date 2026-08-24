#!/usr/bin/env python3
"""为丧尸服 ARCRealisticSurvival 数据库写入 infection_sources 配置。"""
import datetime
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(
    r"C:\Users\DEVIL\Desktop\app\MCBE\Servers\MCBEZombieServer\bedrock_server\plugins\ARCRealisticSurvival\ars_survival.db"
)

# (match_pattern, display_name, infection_delta, match_type)
ZOMBIE_SERVER_INFECTION_SOURCES = [
    # --- 命名空间默认（精确实体优先） ---
    ("zombie:", "生化危机模组默认", 4, "namespace"),
    ("mutant:", "突变生物模组默认", 5, "namespace"),
    ("og_mutant:", "OG突变默认", 7, "namespace"),
    ("js7_mutant:", "JS7突变默认", 8, "namespace"),
    ("minecraft:", "原版生物默认", 2, "namespace"),
    # --- 生化危机 BE ---
    ("zombie:zombie", "普通丧尸", 5, "entity"),
    ("zombie:zombie_runner", "奔跑者", 7, "entity"),
    ("zombie:zombie_conscript", "征召兵", 5, "entity"),
    ("zombie:zombie_fat", "胖子丧尸", 6, "entity"),
    ("zombie:zombie_hunter", "猎手", 8, "entity"),
    ("zombie:zombie_hunter_elita", "精英猎手", 12, "entity"),
    ("zombie:zombie_jumper", "跳跃者", 8, "entity"),
    ("zombie:zombie_spit", "吐酸丧尸", 7, "entity"),
    ("zombie:zombie_longarms", "长臂丧尸", 7, "entity"),
    ("zombie:zombie_wolf", "丧尸狼", 8, "entity"),
    ("zombie:zombie_wolf_elita", "精英丧尸狼", 12, "entity"),
    ("zombie:zombie_butcher", "屠夫", 10, "entity"),
    ("zombie:zombie_marauder", "掠夺者", 10, "entity"),
    ("zombie:zombie_executioner", "处决者", 12, "entity"),
    ("zombie:zombie_mutant", "突变丧尸", 12, "entity"),
    ("zombie:zombie_giant", "巨人丧尸", 15, "entity"),
    ("zombie:mutant", "突变体", 10, "entity"),
    ("zombie:zombie_withered", "枯萎丧尸", 9, "entity"),
    ("zombie:zombie_adapted", "适应者", 9, "entity"),
    ("zombie:zombie_defiler", "亵渎者", 10, "entity"),
    ("zombie:zombie_screamer", "尖叫者", 8, "entity"),
    ("zombie:zombie_spider", "丧尸蜘蛛", 9, "entity"),
    ("zombie:zombie_spider_giant", "巨型丧尸蜘蛛", 15, "entity"),
    ("zombie:zombie_tyrant", "暴君", 20, "entity"),
    ("zombie:zombie_nemesis_1", "追踪者(一阶段)", 18, "entity"),
    ("zombie:zombie_nemesis_2", "追踪者(二阶段)", 20, "entity"),
    ("zombie:parasite", "寄生虫", 6, "entity"),
    ("zombie:soldier0_2285", "士兵丧尸", 8, "entity"),
    # --- 突变生物 BE：丧尸系 ---
    ("mutant:mutant_zombie", "突变僵尸", 7, "entity"),
    ("mutant:mutant_husk", "突变尸壳", 7, "entity"),
    ("mutant:mutant_drowned", "突变溺尸", 7, "entity"),
    ("mutant:mutant_bouldering_zombie", "攀岩僵尸", 8, "entity"),
    ("mutant:mutant_lobber_zombie", "投弹僵尸", 8, "entity"),
    ("mutant:mutant_zombie_pigman", "突变僵尸猪人", 7, "entity"),
    ("mutant:mutant_zombie_villager", "突变僵尸村民", 7, "entity"),
    ("mutant:mutant_zombified_piglin", "突变僵尸猪灵", 7, "entity"),
    ("mutant:zombie_minion", "僵尸仆从", 5, "entity"),
    ("mutant:husk_minion", "尸壳仆从", 5, "entity"),
    ("mutant:drowned_minion", "溺尸仆从", 5, "entity"),
    ("mutant:zombiepig_minion", "猪人仆从", 5, "entity"),
    ("mutant:zombiepiglin_minion", "猪灵仆从", 5, "entity"),
    ("mutant:lobber_zombie_minion", "投弹仆从", 6, "entity"),
    ("mutant:bouldering_zombie_minion", "攀岩仆从", 6, "entity"),
    ("og_mutant:2016_mutant_zombie", "2016突变僵尸", 9, "entity"),
    ("og_mutant:2017_mutant_zombie", "2017突变僵尸", 9, "entity"),
    ("og_mutant:2016_mutant_villager", "2016突变村民", 8, "entity"),
    ("og_mutant:og_zombie_villager", "OG僵尸村民", 8, "entity"),
    ("js7_mutant:zombie_giant", "JS7巨型僵尸", 15, "entity"),
    # --- 突变生物 BE：其他危险突变 ---
    ("mutant:mutant_skeleton", "突变骷髅", 6, "entity"),
    ("mutant:mutant_stray", "突变流浪者", 6, "entity"),
    ("mutant:mutant_bogged", "突变沼骸", 6, "entity"),
    ("mutant:mutant_wither_skeleton", "突变凋灵骷髅", 8, "entity"),
    ("mutant:mutant_creeper", "突变苦力怕", 9, "entity"),
    ("mutant:mutant_enderman", "突变末影人", 10, "entity"),
    ("mutant:mutant_wolf", "突变狼", 7, "entity"),
    ("mutant:mutant_skeleton_wolf", "突变骷髅狼", 8, "entity"),
    ("mutant:mutant_spider_pig", "突变蜘蛛猪", 8, "entity"),
    ("mutant:mutant_piglin", "突变猪灵", 7, "entity"),
    ("mutant:mutant_piglin_brute", "突变猪灵蛮兵", 9, "entity"),
    ("mutant:mutant_iron_golem", "突变铁傀儡", 12, "entity"),
    ("mutant:mutant_vindicator", "突变卫道士", 8, "entity"),
    ("mutant:mutant_pillager", "突变掠夺者", 7, "entity"),
    ("mutant:mutant_evoker", "突变唤魔者", 9, "entity"),
    ("mutant:mutant_vex", "突变恼鬼", 5, "entity"),
    ("mutant:skeleton_wolf", "骷髅狼", 7, "entity"),
    # --- 原版僵尸（兜底） ---
    ("minecraft:zombie", "原版僵尸", 5, "entity"),
    ("minecraft:husk", "原版尸壳", 5, "entity"),
    ("minecraft:drowned", "原版溺尸", 5, "entity"),
    ("minecraft:zombie_villager", "原版僵尸村民", 5, "entity"),
    ("minecraft:zombie_villager_v2", "原版僵尸村民V2", 5, "entity"),
]


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS infection_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_pattern TEXT NOT NULL UNIQUE,
            match_type TEXT NOT NULL,
            infection_delta INTEGER NOT NULL DEFAULT 5,
            display_name TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS player_infection (
            xuid TEXT PRIMARY KEY,
            player_name TEXT NOT NULL,
            infection REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute("DELETE FROM infection_sources")
    now = datetime.datetime.utcnow().isoformat()
    for pattern, name, delta, mtype in ZOMBIE_SERVER_INFECTION_SOURCES:
        cur.execute(
            """
            INSERT INTO infection_sources
            (match_pattern, match_type, infection_delta, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pattern, mtype, delta, name, now, now),
        )
    conn.commit()
    count = cur.execute("SELECT COUNT(*) FROM infection_sources").fetchone()[0]
    conn.close()
    print(f"Seeded {count} infection_sources into {db_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    seed(target)
