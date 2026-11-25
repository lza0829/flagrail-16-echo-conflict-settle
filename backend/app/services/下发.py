import json
from datetime import datetime, timezone

from app.db import 连接
from fastapi import HTTPException


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 下一票(体: dict) -> dict:
    conn = 连接()
    try:
        版本键 = 体["版本键"]
        桶键 = 体["桶键"]
        票号 = 体.get("票号") or f"票-{版本键}-{桶键}"
        ver = conn.execute("SELECT * FROM 开关版本 WHERE 版本键=?", (版本键,)).fetchone()
        if not ver:
            raise HTTPException(400, f"版本未入轨:{版本键}")
        if ver["状态"] not in ("已入轨", "灰度中"):
            raise HTTPException(400, f"版本状态不可下发:{ver['状态']}")
        bucket = conn.execute("SELECT * FROM 分桶规 WHERE 桶键=? AND 状态='生效'", (桶键,)).fetchone()
        if not bucket:
            raise HTTPException(400, f"分桶未生效:{桶键}")
        if bucket["开关键"] != ver["开关键"]:
            raise HTTPException(400, "分桶开关键与版本不一致")
        已 = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if 已 and 已["状态"] == "已下发":
            return {"票号": 票号, "状态": "已下发", "幂等": True}
        conn.execute(
            """INSERT INTO 下发票(票号, 开关键, 版本键, 桶键, 状态, 下发时, 世代, 说明)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(票号) DO UPDATE SET
                 状态='已下发', 下发时=excluded.下发时, 世代=下发票.世代+1""",
            (票号, ver["开关键"], 版本键, 桶键, "已下发", 此刻(), 1, 体.get("说明", "")),
        )
        conn.execute(
            "UPDATE 开关版本 SET 状态='灰度中' WHERE 版本键=?", (版本键,)
        )
        conn.commit()
        return {"票号": 票号, "状态": "已下发", "幂等": False}
    finally:
        conn.close()


def 装下发夹具(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"下发-{此刻()}"
    结果 = []
    for item in 体.get("下发") or []:
        结果.append(下一票(item))
    conn = 连接()
    try:
        报告 = {"类型": "下发", "结果": 结果}
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
