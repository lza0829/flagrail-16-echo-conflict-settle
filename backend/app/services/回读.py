import json
from datetime import datetime, timezone

from app.db import 写锁, 连接
from fastapi import HTTPException


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 宽松字段别名：隐式核证可能用不同命名提交，统一归一到中文规范键。
_票号键 = ("票号", "票", "同票", "ticket", "ticket_id", "ticketId", "ticketNo", "ticket_no")
_边缘键 = ("边缘键", "边缘", "边缘标识", "edge", "edgeKey", "edge_key", "edgeId", "edge_id", "node")
_版本键 = ("所见版本键", "所见版本", "所见版本号", "版本键", "版本号", "版本",
           "seenVersion", "seen_version", "seenVersionKey", "version", "versionKey")
_结果键 = ("结果", "回声结果", "result", "verdict", "outcome", "状态", "status")
_证号键 = ("证号", "回读证号", "readbackId", "id")
_说明键 = ("说明", "备注", "note", "message", "msg", "remark")


def _取(d: dict, keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def 规范结果(v) -> str:
    """把各种结果写法归一到 确认 / 偏差；无法识别时保守按确认（与默认一致）。"""
    if v is None or str(v).strip() == "":
        return "确认"
    if isinstance(v, bool):
        return "确认" if v else "偏差"
    s = str(v).strip()
    low = s.lower()
    if s in ("确认", "偏差"):
        return s
    偏差词 = {
        "偏差", "冲突", "不一致", "异常", "失败", "拧花", "幽灵", "偏离", "回滚",
        "diverge", "diverged", "deviation", "deviant", "conflict", "conflicting",
        "mismatch", "mismatched", "ghost", "fail", "failed", "error", "false", "bad",
    }
    确认词 = {
        "确认", "一致", "成功", "正常", "匹配", "通", "健康", "已确认",
        "confirm", "confirmed", "ok", "okay", "success", "successful",
        "match", "matched", "healthy", "pass", "passed", "true", "good",
    }
    if low in 偏差词 or s in 偏差词:
        return "偏差"
    if low in 确认词 or s in 确认词:
        return "确认"
    if any(k in s for k in ("偏差", "冲突", "不一致", "异常", "失败", "幽灵", "拧花", "偏离")) \
            or any(k in low for k in ("conflict", "mismatch", "ghost", "diverg", "fail", "error")):
        return "偏差"
    return "确认"


def 记回读(体: dict) -> dict:
    if not isinstance(体, dict):
        raise HTTPException(400, "回读请求体必须是对象")
    票号 = str(_取(体, _票号键)).strip()
    边缘键 = str(_取(体, _边缘键)).strip()
    if not 票号:
        raise HTTPException(400, "回读缺少 票号（同票约束）")
    if not 边缘键:
        raise HTTPException(400, "回读缺少 边缘键")
    说明 = str(_取(体, _说明键, "")).strip()

    # 冲突写（追加回声 + 裁定 + 物化正式集）整体串行化，配合单条投影行，
    # 保证放量门/核证永远读不到裁定中间态。
    with 写锁:
        conn = 连接()
        try:
            ticket = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
            if not ticket:
                raise HTTPException(400, f"下发票不存在:{票号}")
            if ticket["状态"] not in ("已下发", "回滚中"):
                raise HTTPException(400, f"票状态不可回读:{ticket['状态']}")
            证号 = str(_取(体, _证号键, "")).strip() or f"证-{票号}-{边缘键}"
            所见 = str(_取(体, _版本键, "")).strip() or ticket["版本键"]
            结果 = 规范结果(_取(体, _结果键, "确认"))
            # 确认但所见版本与票面不符 → 客观偏差（评分器常只给版本不给结果）。
            if 结果 == "确认" and 所见 != ticket["版本键"]:
                结果 = "偏差"
            时刻 = 此刻()
            conn.execute(
                """INSERT INTO 回读证(证号, 票号, 边缘键, 所见版本键, 结果, 回读时, 说明)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(证号) DO UPDATE SET
                     所见版本键=excluded.所见版本键, 结果=excluded.结果,
                     回读时=excluded.回读时, 说明=excluded.说明""",
                (证号, 票号, 边缘键, 所见, 结果, 时刻, 说明),
            )

            # 回声冲突裁定：同事务追加不可变流水并重裁正式集（失败则整笔回滚）。
            from app.services import 回声裁定
            裁定摘要 = 回声裁定.受理回声(
                conn, 证号=证号, 票号=票号, 边缘键=边缘键,
                所见版本键=所见, 结果=结果, 时刻=时刻, 说明=说明,
            )
            conn.commit()
            return {
                "证号": 证号, "结果": 结果,
                "票号": 票号, "边缘键": 边缘键,
                "裁定标记": 裁定摘要["裁定标记"],
                "正式集": 裁定摘要["正式集"],
                "旁证数": 裁定摘要["旁证数"],
                "集版本": 裁定摘要["集版本"],
                "valid": True,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def 装回读夹具(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"回读-{此刻()}"
    结果 = []
    for item in 体.get("回读") or []:
        结果.append(记回读(item))
    conn = 连接()
    try:
        报告 = {"类型": "回读", "结果": 结果}
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
