from datetime import datetime, timezone

from app.db import 连接


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 跑贯验() -> dict:
    conn = 连接()
    try:
        版本数 = conn.execute("SELECT COUNT(*) FROM 开关版本").fetchone()[0]
        下发数 = conn.execute("SELECT COUNT(*) FROM 下发票").fetchone()[0]
        回读数 = conn.execute("SELECT COUNT(*) FROM 回读证").fetchone()[0]
        命运数 = conn.execute("SELECT COUNT(*) FROM 命运账").fetchone()[0]

        问题 = []
        # 已下发票必须有版本
        for r in conn.execute("SELECT 票号, 版本键 FROM 下发票 WHERE 状态='已下发'"):
            if not conn.execute(
                "SELECT 1 FROM 开关版本 WHERE 版本键=?", (r["版本键"],)
            ).fetchone():
                问题.append(f"悬空下发:{r['票号']}")
        # 确认回读必须对应已下发/已回滚票
        for r in conn.execute("SELECT 证号, 票号, 所见版本键, 结果 FROM 回读证 WHERE 结果='确认'"):
            t = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (r["票号"],)).fetchone()
            if not t:
                问题.append(f"悬空回读:{r['证号']}")
            elif t["版本键"] != r["所见版本键"] and t["状态"] == "已下发":
                问题.append(f"回读版本拧花:{r['证号']}")
        # 已回滚票应有命运条
        for r in conn.execute("SELECT 票号 FROM 下发票 WHERE 状态='已回滚'"):
            if not conn.execute(
                "SELECT 1 FROM 命运账 WHERE 对象类型='下发票' AND 对象键=? AND 命运='回滚'",
                (r["票号"],),
            ).fetchone():
                问题.append(f"回滚无命运:{r['票号']}")

        通过 = 1 if not 问题 else 0
        说明 = "齐" if 通过 else "; ".join(问题)
        conn.execute(
            """INSERT INTO 轨贯验(时刻, 版本数, 下发数, 回读数, 命运数, 是否通过, 说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 版本数, 下发数, 回读数, 命运数, 通过, 说明),
        )
        conn.commit()
        return {
            "是否通过": bool(通过),
            "版本数": 版本数,
            "下发数": 下发数,
            "回读数": 回读数,
            "命运数": 命运数,
            "说明": 说明,
        }
    finally:
        conn.close()
