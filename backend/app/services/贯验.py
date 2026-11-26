import json
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

        # —— 回声冲突裁定协同校验：裁定结果 / 正式证 / 放量门共用的正式集必须自洽 ——
        from app.services import 回声裁定
        策略 = 回声裁定.当前策略(conn)
        票s = [r["票号"] for r in conn.execute(
            "SELECT DISTINCT 票号 FROM 回声流水"
        )]
        for t in 票s:
            rows = [dict(x) for x in conn.execute(
                "SELECT * FROM 回声流水 WHERE 票号=? ORDER BY 序号 ASC", (t,)
            )]
            verdict = 回声裁定.裁定时序(t, rows, 策略)
            p = conn.execute("SELECT * FROM 正式集 WHERE 票号=?", (t,)).fetchone()
            if p is None:
                问题.append(f"回声双结果无裁决(缺正式集投影):{t}")
                continue
            存集 = json.loads(p["正式集"])
            if sorted(存集) != sorted(verdict["正式集"]):
                问题.append(
                    f"回声裁定正式集漂移:{t} {存集}!={verdict['正式集']}"
                )
            if p["策略值"] != 策略:
                问题.append(f"回声裁定口径未物化一致:{t}")
            旁证n = conn.execute(
                "SELECT COUNT(*) c FROM 旁证历史 WHERE 策略值=? AND 票号=?",
                (策略, t),
            ).fetchone()["c"]
            if p["旁证数"] != 旁证n:
                问题.append(f"旁证数与旁证历史不符:{t}")
            证缘 = [r["边缘键"] for r in conn.execute(
                "SELECT 边缘键 FROM 正式确认证 WHERE 票号=? AND 状态='有效'", (t,)
            )]
            if sorted(证缘) != sorted(verdict["正式集"]):
                问题.append(f"正式证未共用裁定正式集:{t}")
            # 同边缘确有确认+偏差，却没有任何旁证/裁定痕迹 → 禁止双结果无裁决
            for e in verdict["冲突缘"]:
                if not conn.execute(
                    "SELECT 1 FROM 旁证历史 WHERE 策略值=? AND 票号=? AND 边缘键=? LIMIT 1",
                    (策略, t, e),
                ).fetchone():
                    # 末次覆盖下，冲突边缘的前序回声必为旁证；若无则说明未物化
                    问题.append(f"同边缘回声冲突无裁决痕迹:{t}/{e}")

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
            "valid": bool(通过),
            "版本数": 版本数,
            "下发数": 下发数,
            "回读数": 回读数,
            "命运数": 命运数,
            "说明": 说明,
        }
    finally:
        conn.close()
