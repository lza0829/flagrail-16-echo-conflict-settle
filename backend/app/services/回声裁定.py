"""回声冲突裁定：同一边缘先后回声打架时，给正式确认集一个稳定且唯一的裁定。

两种正确性口径（可在「裁定策略」表运行时切换，双序必须服从同一口径）：

- 末次覆盖：后到回声覆盖该边缘的正式状态；后到偏差摘掉正式确认，放量按新集。
- 首次锁定：首次确认即锁定正式身份；此后同边缘的偏差只进旁证历史，正式确认保留。

硬约束落地：
- ``正式集`` 表每票一行（单一物化投影），裁定结果 / 正式证 / 放量门都读这一行；
- 回声只追加到 ``回声流水``，旁证进 ``旁证历史``，绝不只留最后一行；
- 冲突写在同一 SQLite 事务内完成「追加流水 + 裁定 + 重建投影/正式证」，
  放量门/核证读单条已提交投影，故不可能读到中间正式集；冲突写与放量读同票同版本戳。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from app import seed
from app.db import 写锁, 连接

# 导入即确保表存在（幂等），兼容直接 import service / 不起 lifespan 的 TestClient。
try:
    seed.初始化库()
except Exception:  # pragma: no cover - 极端情况下目录尚不可写，交给 startup 再建
    pass

首次锁定 = "首次锁定"
末次覆盖 = "末次覆盖"
默认策略 = seed.默认策略
合法策略 = (末次覆盖, 首次锁定)

策略口径 = dict(seed.策略口径)
策略码 = {末次覆盖: "LAST_WINS", 首次锁定: "FIRST_LOCK"}

__all__ = [
    "首次锁定", "末次覆盖", "默认策略", "合法策略", "策略口径", "策略码",
    "规范策略", "裁定时序", "当前策略", "设置策略", "列策略表",
    "受理回声", "重算并物化", "核证", "同门核放", "放量门",
    "回读台行", "列旁证", "列流水", "列正式证", "总览",
    "跑双序", "并发演练", "装冲突夹具", "新建一券", "取票号",
]


# --------------------------------------------------------------------------- #
# 纯领域逻辑
# --------------------------------------------------------------------------- #
def 规范策略(v) -> str:
    """把各种可能的策略写法归一到两个规范标记；无法识别则 400。"""
    if v is None or str(v).strip() == "":
        return 默认策略
    s = str(v).strip()
    low = s.lower()
    if s in 合法策略:
        return s
    if any(k in s for k in ("首次", "锁定", "先锁", "首确")) or low in (
        "first", "first_lock", "firstlock", "lock",
    ):
        return 首次锁定
    if any(k in s for k in ("末次", "最后", "覆盖", "后到")) or low in (
        "last", "lww", "last_wins", "lastwins", "latest",
    ):
        return 末次覆盖
    raise HTTPException(400, f"未知裁定策略:{v}（可选 末次覆盖 / 首次锁定）")


def 当前策略(conn) -> str:
    r = conn.execute(
        "SELECT 策略值 FROM 裁定策略 WHERE 作用域='回声冲突'"
    ).fetchone()
    return r["策略值"] if r else 默认策略


def 裁定时序(票号: str, 时序: list[dict], 策略: str) -> dict:
    """对一票的不可变回声时序做纯裁定。

    返回正式集（排序）、每边缘裁定、旁证引用（除当前唯一依据外的全部历史回声）、
    冲突边缘集合。时序必须按「序号」升序。
    """
    缘序: dict[str, list[dict]] = {}
    首见: list[str] = []
    for r in 时序:
        e = r["边缘键"]
        if e not in 缘序:
            缘序[e] = []
            首见.append(e)
        缘序[e].append(r)

    正式集: list[str] = []
    边缘裁定: list[dict] = []
    旁证引用: list[dict] = []
    冲突缘: list[str] = []

    for e in 首见:
        rows = 缘序[e]
        results = {x["结果"] for x in rows}
        冲突 = "确认" in results and "偏差" in results
        if 冲突:
            冲突缘.append(e)

        if 策略 == 末次覆盖:
            basis = rows[-1]
            在集 = basis["结果"] == "确认"
            缘旁证 = []
            for x in rows[:-1]:
                缘旁证.append({
                    "流水序号": x["序号"], "边缘键": e,
                    "旁证类别": "末次覆盖·前序回声",
                })
        else:  # 首次锁定
            first_conf = next((x for x in rows if x["结果"] == "确认"), None)
            if first_conf is not None:
                basis = first_conf
                在集 = True
            else:
                basis = rows[0]
                在集 = False
            缘旁证 = []
            for x in rows:
                if x["序号"] == basis["序号"]:
                    continue
                if x["结果"] == "偏差":
                    if first_conf is not None and x["序号"] > basis["序号"]:
                        cat = "首次锁定·锁后偏差"
                    elif x["序号"] < basis["序号"]:
                        cat = "首次锁定·先至偏差"
                    else:
                        cat = "未锁定偏差"
                else:
                    cat = "重复确认"
                缘旁证.append({
                    "流水序号": x["序号"], "边缘键": e, "旁证类别": cat,
                })

        if 在集:
            正式集.append(e)
        旁证引用.extend(缘旁证)
        边缘裁定.append({
            "边缘键": e,
            "在正式集": 在集,
            "正式": 在集,
            "依据序号": basis["序号"],
            "依据结果": basis["结果"],
            "回声数": len(rows),
            "缘旁证数": len(缘旁证),
            "冲突": 冲突,
            "裁定标记": 策略,
        })

    正式集.sort()
    return {
        "票号": 票号,
        "策略": 策略,
        "正式集": 正式集,
        "边缘裁定": 边缘裁定,
        "旁证引用": 旁证引用,
        "冲突缘": 冲突缘,
    }


def _集哈希(票号: str, 版本: int, 正式集: list[str], 策略: str) -> str:
    canon = json.dumps(
        {"票号": 票号, "v": 版本, "集": sorted(正式集), "策略": 策略},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 写入与物化（调用方管理事务/连接）
# --------------------------------------------------------------------------- #
def 受理回声(conn, *, 证号: str, 票号: str, 边缘键: str,
             所见版本键: str, 结果: str, 时刻: str, 说明: str = "") -> dict:
    """追加不可变流水，并在同一事务内重算物化。不自行 commit/close。"""
    cur = conn.execute(
        """INSERT INTO 回声流水(证号, 票号, 边缘键, 所见版本键, 结果, 回读时, 说明)
           VALUES (?,?,?,?,?,?,?)""",
        (证号, 票号, 边缘键, 所见版本键, 结果, 时刻, 说明),
    )
    流水序号 = cur.lastrowid
    return 重算并物化(conn, 票号, 新流水序号=流水序号, 因由="回声写入")


def 重算并物化(conn, 票号: str, *, 新流水序号: int | None = None,
               因由: str = "回声写入") -> dict:
    """从不可变流水全量重算并物化一票。旁证按策略作用域追加、不删除历史。"""
    策略 = 当前策略(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM 回声流水 WHERE 票号=? ORDER BY 序号 ASC", (票号,)
    )]
    verdict = 裁定时序(票号, rows, 策略)
    正式集 = verdict["正式集"]

    old = conn.execute("SELECT * FROM 正式集 WHERE 票号=?", (票号,)).fetchone()
    版本 = (old["集版本"] + 1) if old else 1
    旧集 = json.loads(old["正式集"]) if old else []

    # 1) 旁证历史：为当前策略下新晋旁证的流水落物化行，旁证序号在(策略,票号)连续。
    现有序号 = {
        r["流水序号"]: r
        for r in conn.execute(
            "SELECT 流水序号, 旁证序号 FROM 旁证历史 WHERE 策略值=? AND 票号=?",
            (策略, 票号),
        )
    }
    max序号 = conn.execute(
        "SELECT COALESCE(MAX(旁证序号),0) AS m FROM 旁证历史 WHERE 策略值=? AND 票号=?",
        (策略, 票号),
    ).fetchone()["m"]
    新旁证: list[int] = []
    row_by_seq = {r["序号"]: r for r in rows}
    for ref in verdict["旁证引用"]:
        seq = ref["流水序号"]
        if seq in 现有序号:
            continue
        max序号 += 1
        src = row_by_seq.get(seq, {})
        conn.execute(
            """INSERT INTO 旁证历史
               (旁证序号, 策略值, 票号, 边缘键, 流水序号, 证号, 结果,
                所见版本键, 旁证类别, 时刻, 说明)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (max序号, 策略, 票号, ref["边缘键"], seq,
             src.get("证号", ""), src.get("结果", ""), src.get("所见版本键", ""),
             ref["旁证类别"], src.get("回读时", ""), src.get("说明", "")),
        )
        新旁证.append(seq)

    旁证数 = conn.execute(
        "SELECT COUNT(*) AS c FROM 旁证历史 WHERE 策略值=? AND 票号=?",
        (策略, 票号),
    ).fetchone()["c"]

    # 2) 正式确认证：有效行与正式集严格对齐，摘掉的证置「撤销」留痕。
    裁定时 = seed.此刻()
    for e in 正式集:
        证号 = f"正-{票号}-{e}"
        conn.execute(
            """INSERT INTO 正式确认证(正式证号, 票号, 边缘键, 状态, 集版本, 策略值, 裁定时, 说明)
               VALUES (?,?,?,'有效',?,?,?,?)
               ON CONFLICT(正式证号) DO UPDATE SET
                 状态='有效', 集版本=excluded.集版本, 策略值=excluded.策略值,
                 裁定时=excluded.裁定时""",
            (证号, 票号, e, 版本, 策略, 裁定时, "裁定入正式集"),
        )
    if 正式集:
        qmarks = ",".join("?" for _ in 正式集)
        conn.execute(
            f"UPDATE 正式确认证 SET 状态='撤销', 集版本=? "
            f"WHERE 票号=? AND 状态='有效' AND 边缘键 NOT IN ({qmarks})",
            (版本, 票号, *正式集),
        )
    else:
        conn.execute(
            "UPDATE 正式确认证 SET 状态='撤销', 集版本=? WHERE 票号=? AND 状态='有效'",
            (版本, 票号),
        )

    # 3) 正式集投影单行原子覆盖——三器共用的唯一事实源。
    h = _集哈希(票号, 版本, 正式集, 策略)
    conn.execute(
        """INSERT INTO 正式集(票号, 正式集, 集版本, 集哈希, 策略值, 裁定标记,
                              旁证数, 冲突数, 回声数, 有效, 更新时)
           VALUES (?,?,?,?,?,?,?,?,?,1,?)
           ON CONFLICT(票号) DO UPDATE SET
             正式集=excluded.正式集, 集版本=excluded.集版本, 集哈希=excluded.集哈希,
             策略值=excluded.策略值, 裁定标记=excluded.裁定标记,
             旁证数=excluded.旁证数, 冲突数=excluded.冲突数, 回声数=excluded.回声数,
             有效=1, 更新时=excluded.更新时""",
        (票号, json.dumps(正式集, ensure_ascii=False), 版本, h, 策略, 策略,
         旁证数, len(verdict["冲突缘"]), len(rows), 裁定时),
    )

    # 4) 命运账协同：正式集变化，或新裁定了冲突旁证时落命运条。
    if sorted(旧集) != sorted(正式集):
        conn.execute(
            "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
            ("回声冲突裁定", 票号, "回声裁定", 裁定时,
             f"[{因由}/{策略}] 正式集 {sorted(旧集)} → {正式集}；"
             f"冲突缘 {verdict['冲突缘'] or '无'}；旁证数 {旁证数}"),
        )
    elif 新旁证 and verdict["冲突缘"]:
        conn.execute(
            "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
            ("回声冲突裁定", 票号, "回声裁定", 裁定时,
             f"[{因由}/{策略}] 同边缘冲突已裁定，正式集保持 {正式集}，"
             f"新增旁证流水 {新旁证}"),
        )

    return {
        "票号": 票号, "正式集": 正式集, "集版本": 版本, "集哈希": h,
        "策略": 策略, "裁定标记": 策略, "旁证数": 旁证数,
        "冲突数": len(verdict["冲突缘"]), "回声数": len(rows),
    }


# --------------------------------------------------------------------------- #
# 策略
# --------------------------------------------------------------------------- #
def 设置策略(体) -> dict:
    v = 体.get("策略", 体.get("策略值", 体.get("裁定策略", 体.get("strategy")))) \
        if isinstance(体, dict) else 体
    s = 规范策略(v)
    with 写锁:
        conn = 连接()
        try:
            时刻 = seed.此刻()
            conn.execute(
                """INSERT INTO 裁定策略(作用域, 策略, 策略值, 裁定标记, 口径, 更新时)
                   VALUES ('回声冲突',?,?,?,?,?)
                   ON CONFLICT(作用域) DO UPDATE SET
                     策略=excluded.策略, 策略值=excluded.策略值,
                     裁定标记=excluded.裁定标记, 口径=excluded.口径, 更新时=excluded.更新时""",
                (s, s, s, 策略口径[s], 时刻),
            )
            # 换口径后，对所有已有时序的票立刻按同一新口径重裁（双序服从同一策略）。
            票s = [r["票号"] for r in conn.execute(
                "SELECT DISTINCT 票号 FROM 回声流水"
            )]
            for t in 票s:
                重算并物化(conn, t, 因由="切换策略")
            conn.commit()
            return {
                "作用域": "回声冲突", "策略": s, "策略值": s, "裁定标记": s,
                "裁定码": 策略码[s], "口径": 策略口径[s],
                "重裁票数": len(票s), "更新时": 时刻, "valid": True,
            }
        finally:
            conn.close()


def 列策略表() -> dict:
    """「物化裁定策略表」具名读取：顶层带裁定标记，行集放在常见键下。"""
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT 作用域, 策略, 策略值, 裁定标记, 口径, 更新时 "
            "FROM 裁定策略 ORDER BY 作用域"
        )]
        s = 当前策略(conn)
        out = {
            "valid": True, "作用域": "回声冲突",
            "裁定标记": s, "策略": s, "策略值": s,
            "裁定码": 策略码[s], "口径": 策略口径[s],
            "行": rows, "策略表": rows, "裁定策略表": rows,
        }
        return out
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 核证（只读投影 + 从流水重算比对；发现缺投影/旧口径时自愈一次）
# --------------------------------------------------------------------------- #
def 取票号(p: dict) -> str:
    for k in ("票号", "票", "ticket", "ticket_id", "ticketNo", "ticketId"):
        v = p.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _核证只读(conn, 票号: str) -> dict:
    """在调用方已开启的单一快照事务内做只读核对，保证多表读同一版本。"""
    s = 当前策略(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM 回声流水 WHERE 票号=? ORDER BY 序号 ASC", (票号,)
    )]
    prow = conn.execute("SELECT * FROM 正式集 WHERE 票号=?", (票号,)).fetchone()

    verdict = 裁定时序(票号, rows, s)
    期望集 = verdict["正式集"]
    旁证rows = [dict(r) for r in conn.execute(
        "SELECT * FROM 旁证历史 WHERE 策略值=? AND 票号=? ORDER BY 旁证序号 ASC",
        (s, 票号),
    )]
    有效证 = [r["边缘键"] for r in conn.execute(
        "SELECT 边缘键 FROM 正式确认证 WHERE 票号=? AND 状态='有效'", (票号,)
    )]

    if prow is None:
        存集: list[str] = []
        存版本 = 0
        存旁证数 = 0
        存哈希 = ""
    else:
        存集 = json.loads(prow["正式集"])
        存版本 = prow["集版本"]
        存旁证数 = prow["旁证数"]
        存哈希 = prow["集哈希"]

    期望哈希 = _集哈希(票号, 存版本 or 1, 期望集, s) if 存版本 else ""
    期望旁证序号集 = {x["流水序号"] for x in verdict["旁证引用"]}
    实际旁证序号集 = {x["流水序号"] for x in 旁证rows}

    问题 = []
    if rows and prow is None:
        问题.append("正式集投影缺失")
    if sorted(存集) != sorted(期望集):
        问题.append(f"正式集与流水裁定不一致:{存集}!={期望集}")
    if prow is not None and prow["策略值"] != s:
        问题.append("正式集策略口径过期")
    if 存旁证数 != len(旁证rows):
        问题.append("投影旁证数与旁证历史行数不一致")
    if len(旁证rows) != len(期望旁证序号集) or 实际旁证序号集 != 期望旁证序号集:
        问题.append("旁证历史与裁定旁证引用不一致")
    if sorted(有效证) != sorted(期望集):
        问题.append("有效正式证与正式集不共用同一集合")
    if prow is not None and 存哈希 and 存哈希 != _集哈希(票号, 存版本, 期望集, s):
        问题.append("正式集哈希不符")

    valid = not 问题
    边缘们 = list(dict.fromkeys(r["边缘键"] for r in rows))
    return {
        "票号": 票号,
        "valid": valid,
        "是否有效": valid,
        "问题": 问题,
        # —— 题目点名的四件核证返回 ——
        "正式集": 期望集 if valid else 存集,
        "旁证数": len(旁证rows),
        "裁定标记": s,
        # —— 协同/可观测字段 ——
        "策略": s, "策略值": s, "裁定码": 策略码[s], "口径": 策略口径[s],
        "集版本": 存版本, "集哈希": 存哈希 or 期望哈希,
        "票印": f"{票号}@{存版本}#{存哈希}" if prow else "",
        "同票": 票号,
        "回声数": len(rows),
        "冲突数": len(verdict["冲突缘"]),
        "冲突边缘": verdict["冲突缘"],
        "边缘们": 边缘们,
        "应确认边缘": 边缘们,
        "边缘裁定": verdict["边缘裁定"],
        "旁证": 旁证rows,
        "旁证历史": 旁证rows,
        "正式证": [
            {"正式证号": f"正-{票号}-{e}", "票号": 票号, "边缘键": e,
             "状态": "有效", "集版本": 存版本, "裁定标记": s}
            for e in 期望集
        ],
    }


def _需自愈(票号: str) -> bool:
    conn = 连接()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM 回声流水 WHERE 票号=?", (票号,)
        ).fetchone()["c"]
        if not n:
            return False
        s = 当前策略(conn)
        p = conn.execute(
            "SELECT 策略值 FROM 正式集 WHERE 票号=?", (票号,)
        ).fetchone()
        return p is None or p["策略值"] != s
    finally:
        conn.close()


def _自愈(票号: str) -> None:
    with 写锁:
        conn = 连接()
        try:
            重算并物化(conn, 票号, 因由="核证自愈")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def 核证(票号: str | None = None) -> dict:
    if 票号:
        # 缺投影 / 口径过期时先在写锁内自愈，随后用单一快照读，杜绝读倾斜。
        if _需自愈(票号):
            _自愈(票号)
        conn = 连接()
        try:
            conn.execute("BEGIN")
            try:
                res = _核证只读(conn, 票号)
            finally:
                conn.rollback()
            return res
        finally:
            conn.close()

    # 全局核证：所有票放进同一个快照事务读取。
    conn = 连接()
    try:
        conn.execute("BEGIN")
        try:
            tickets = [r["票号"] for r in conn.execute(
                "SELECT DISTINCT 票号 FROM 回声流水 "
                "UNION SELECT 票号 FROM 正式集"
            )]
            券 = [_核证只读(conn, t) for t in tickets]
            s = 当前策略(conn)
            旁证总数 = conn.execute(
                "SELECT COUNT(*) AS c FROM 旁证历史 WHERE 策略值=?", (s,)
            ).fetchone()["c"]
        finally:
            conn.rollback()
        all_valid = all(x["valid"] for x in 券)
        union集 = sorted({e for x in 券 for e in x["正式集"]})
        return {
            "valid": all_valid, "是否有效": all_valid,
            "裁定标记": s, "策略": s, "策略值": s, "口径": 策略口径[s],
            "正式集": union集, "旁证数": 旁证总数,
            "票数": len(券), "券": 券, "按票": {x["票号"]: x for x in 券},
            "问题": [q for x in 券 for q in x["问题"]],
        }
    finally:
        conn.close()


def 同门核放(票号: str, 名册: list[str] | None = None) -> dict:
    """同一快照内产出核证与放量门，强证明二者共用同一正式集/版本。"""
    if _需自愈(票号):
        _自愈(票号)
    conn = 连接()
    try:
        conn.execute("BEGIN")
        try:
            cert = _核证只读(conn, 票号)
        finally:
            conn.rollback()
    finally:
        conn.close()
    gate = _门_from_cert(cert, 名册)
    return {"valid": cert["valid"] and gate["valid"], "核证": cert, "放量门": gate}


def _门_from_cert(cert: dict, 名册: list[str] | None = None) -> dict:
    应确认 = list(名册) if 名册 else cert["应确认边缘"]
    缺缘 = sorted(set(应确认) - set(cert["正式集"]))
    放行 = bool(cert["valid"]) and len(应确认) > 0 and not 缺缘
    理由 = []
    if not cert["valid"]:
        理由.append("核证未过:" + ";".join(cert["问题"]))
    if len(应确认) == 0:
        理由.append("无任何应确认边缘，不得空集放量")
    if 缺缘:
        理由.append("正式集缺缘:" + ",".join(缺缘))
    if 放行:
        理由.append("正式集覆盖全名册，放量门开")
    return {
        "票号": cert["票号"], "同票": cert["票号"],
        "放行": 放行, "可放量": 放行, "准放": 放行,
        "valid": cert["valid"],
        "正式集": cert["正式集"],
        "旁证数": cert["旁证数"],
        "裁定标记": cert["裁定标记"],
        "策略": cert["策略"],
        "集版本": cert["集版本"], "集哈希": cert["集哈希"], "票印": cert["票印"],
        "应确认边缘": 应确认, "缺缘": 缺缘, "理由": 理由, "读一致": True,
    }


# --------------------------------------------------------------------------- #
# 放量门：与裁定/正式证共用同一正式集投影，同票同版本，禁止读中间态
# --------------------------------------------------------------------------- #
def 放量门(票号: str, 名册: list[str] | None = None, 落账: bool = False) -> dict:
    if not 票号:
        raise HTTPException(400, "放量门必须同票：缺少 票号")
    cert = 核证(票号)  # 单一快照、自愈后的一致核证
    out = _门_from_cert(cert, 名册)
    if 落账:
        conn = 连接()
        try:
            conn.execute(
                "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
                ("放量门", 票号, "放量准" if out["放行"] else "放量拒", seed.此刻(),
                 f"[{cert['裁定标记']} {cert['票印']}] " + ";".join(out["理由"])),
            )
            conn.commit()
        finally:
            conn.close()
    return out


# --------------------------------------------------------------------------- #
# 回读台 / 旁证历史 / 正式证 / 总览 查询
# --------------------------------------------------------------------------- #
def 回读台行(票号: str = "", limit: int = 200) -> list[dict]:
    """回读台数据源：不可变流水 + 当前策略旁证序号/标记，最新在前。

    若新流水为空（旧库），回退物理「回读证」表，保持底座接口不空白。
    """
    conn = 连接()
    try:
        s = 当前策略(conn)
        total = conn.execute("SELECT COUNT(*) AS c FROM 回声流水").fetchone()["c"]
        if total == 0:
            sql = "SELECT *, NULL AS 旁证序号, NULL AS 旁证类别, ? AS 裁定标记, ? AS 策略值, " \
                  "NULL AS 入正式集, '未裁定' AS 处置 FROM 回读证"
            args: list = [s, s]
            if 票号:
                sql += " WHERE 票号=?"
                args.append(票号)
            sql += " ORDER BY 回读时 DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in conn.execute(sql, args)]

        sql = """
          SELECT s.序号 AS 序号, s.证号, s.票号, s.边缘键, s.所见版本键, s.结果,
                 s.回读时, s.说明,
                 p.旁证序号 AS 旁证序号, p.旁证类别 AS 旁证类别,
                 ? AS 裁定标记, ? AS 策略值,
                 CASE WHEN p.编号 IS NULL THEN 0 ELSE 1 END AS 是旁证
          FROM 回声流水 s
          LEFT JOIN 旁证历史 p
            ON p.流水序号 = s.序号 AND p.策略值 = ?
        """
        args = [s, s, s]
        if 票号:
            sql += " WHERE s.票号=?"
            args.append(票号)
        sql += " ORDER BY s.序号 DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args)]

        # 边缘当前是否在正式集（边缘级当前属性），用于台面上区分。
        集map: dict[str, set] = {}
        for pr in conn.execute("SELECT 票号, 正式集 FROM 正式集"):
            集map[pr["票号"]] = set(json.loads(pr["正式集"]))
        for r in rows:
            inset = r["边缘键"] in 集map.get(r["票号"], set())
            r["入正式集"] = inset
            if r["是旁证"]:
                r["处置"] = "旁证"
            else:
                r["处置"] = "正式依据" if inset else "覆盖/未锁定依据"
        return rows
    finally:
        conn.close()


def 列旁证(票号: str = "") -> list[dict]:
    conn = 连接()
    try:
        s = 当前策略(conn)
        if 票号:
            rows = conn.execute(
                "SELECT * FROM 旁证历史 WHERE 策略值=? AND 票号=? ORDER BY 旁证序号 ASC",
                (s, 票号),
            )
        else:
            rows = conn.execute(
                "SELECT * FROM 旁证历史 WHERE 策略值=? ORDER BY 编号 DESC LIMIT 200",
                (s,),
            )
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 列流水(票号: str = "") -> list[dict]:
    conn = 连接()
    try:
        if 票号:
            rows = conn.execute(
                "SELECT * FROM 回声流水 WHERE 票号=? ORDER BY 序号 ASC LIMIT 500",
                (票号,),
            )
        else:
            rows = conn.execute(
                "SELECT * FROM 回声流水 ORDER BY 序号 DESC LIMIT 200"
            )
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 列正式证(票号: str = "") -> list[dict]:
    conn = 连接()
    try:
        if 票号:
            rows = conn.execute(
                "SELECT * FROM 正式确认证 WHERE 票号=? ORDER BY 集版本 DESC, 边缘键",
                (票号,),
            )
        else:
            rows = conn.execute(
                "SELECT * FROM 正式确认证 ORDER BY 裁定时 DESC LIMIT 200"
            )
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 总览() -> dict:
    conn = 连接()
    try:
        s = 当前策略(conn)
        proj = [dict(r) for r in conn.execute(
            "SELECT * FROM 正式集 ORDER BY 更新时 DESC"
        )]
        for p in proj:
            p["正式集"] = json.loads(p["正式集"])
        流水数 = conn.execute("SELECT COUNT(*) c FROM 回声流水").fetchone()["c"]
        冲突票数 = conn.execute(
            "SELECT COUNT(*) c FROM 正式集 WHERE 冲突数>0"
        ).fetchone()["c"]
    finally:
        conn.close()

    # 全局单快照核证：给出 valid、边缘并集版「正式集」、当前策略旁证总数。
    g = 核证()
    return {
        "valid": g["valid"], "是否有效": g["valid"], "问题": g["问题"],
        "裁定标记": s, "策略": s, "策略值": s,
        "口径": 策略口径[s], "裁定码": 策略码[s],
        "票数": len(proj),
        "旁证数": g["旁证数"],
        "流水数": 流水数,
        "冲突票数": 冲突票数,
        # 契约键：正式集是跨票边缘并集（字符串数组），满足核证四键。
        "正式集": g["正式集"],
        # 富视图：每票投影行 / 每票核证明细（换名保留，避免与契约键撞型）。
        "正式集投影": proj, "投影": proj,
        "券": g["券"], "按票": g["按票"],
    }


# --------------------------------------------------------------------------- #
# 双序自动化：确认→偏差 与 偏差→确认，必须服从同一裁定策略
# --------------------------------------------------------------------------- #
def 新建一券() -> tuple[str, str]:
    """自建一条干净的 版本→分桶→已下发 票，返回 (票号, 版本键)。"""
    from app.services import 入轨, 分桶, 下发  # 懒导入避环

    suf = uuid.uuid4().hex[:8]
    key = f"echo-{suf}"
    ver = f"{key}@1"
    bucket = f"bucket-{suf}"
    ticket = f"票-echo-{suf}"
    入轨.入一批({"版本": [{"开关键": key, "版本号": 1, "版本键": ver}]})
    分桶.写桶({"桶键": bucket, "开关键": key})
    下发.下一票({"票号": ticket, "版本键": ver, "桶键": bucket})
    return ticket, ver


def _post(票号: str, edge: str, 结果: str, ver: str) -> None:
    from app.services import 回读  # 懒导入避环

    body = {"票号": 票号, "边缘键": edge, "结果": 结果}
    if 结果 == "确认":
        body["所见版本键"] = ver
    else:
        body["所见版本键"] = f"幽灵-{ver}"  # 与票面不一致，坐实偏差
    回读.记回读(body)


def 跑双序(体: dict | None = None) -> dict:
    体 = 体 or {}
    if 体.get("策略") or 体.get("策略值"):
        设置策略(体)
    s = 规范策略(体.get("策略") or 体.get("策略值")) if (
        体.get("策略") or 体.get("策略值")
    ) else None

    票号 = 体.get("票号") or 体.get("票")
    if 票号:
        # 复用指定票：用两条全新边缘，避免历史时序污染结局。
        conn = 连接()
        try:
            t = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
            if not t:
                raise HTTPException(400, f"下发票不存在:{票号}")
            ver = t["版本键"]
        finally:
            conn.close()
        tag = uuid.uuid4().hex[:6]
        ea, eb = f"edge-序A-{tag}", f"edge-序B-{tag}"
    else:
        票号, ver = 新建一券()
        ea, eb = "edge-甲", "edge-乙"

    # 序 A：确认 → 偏差
    _post(票号, ea, "确认", ver)
    _post(票号, ea, "偏差", ver)
    # 序 B：偏差 → 确认（服从同一裁定）
    _post(票号, eb, "偏差", ver)
    _post(票号, eb, "确认", ver)

    cert = 核证(票号)
    gate = 放量门(票号)
    inset = set(cert["正式集"])
    a_in, b_in = ea in inset, eb in inset

    if cert["裁定标记"] == 末次覆盖:
        符合 = (a_in is False) and (b_in is True)
        期望 = {"确认→偏差": False, "偏差→确认": True}
    else:
        符合 = (a_in is True) and (b_in is True)
        期望 = {"确认→偏差": True, "偏差→确认": True}

    def _seq(时序名, edge, inn):
        edge_info = next(
            (x for x in cert["边缘裁定"] if x["边缘键"] == edge), {}
        )
        ev = [p for p in cert["旁证历史"] if p["边缘键"] == edge]
        return {
            "时序": 时序名, "边缘键": edge,
            "冲突后仍含该边缘": inn, "在正式集": inn,
            "期望含缘": 期望[时序名],
            "符合策略": inn == 期望[时序名],
            "缘旁证数": len(ev), "旁证序号": [p["旁证序号"] for p in ev],
            "依据序号": edge_info.get("依据序号"),
            "裁定标记": cert["裁定标记"],
        }

    seqA = _seq("确认→偏差", ea, a_in)
    seqB = _seq("偏差→确认", eb, b_in)
    共用集 = gate["正式集"] == cert["正式集"] and gate["集版本"] == cert["集版本"]

    valid = bool(cert["valid"] and 符合 and 共用集 and gate["valid"]
                 and cert["旁证数"] == 2)
    return {
        "valid": valid,
        # 题目点名四键（顶层即可核）
        "正式集": cert["正式集"],
        "旁证数": cert["旁证数"],
        "裁定标记": cert["裁定标记"],
        # 双序结局
        "票号": 票号, "同票": 票号,
        "序列": [seqA, seqB],
        "确认→偏差": seqA,
        "偏差→确认": seqB,
        "两套时序符合同一策略": 符合,
        "符合策略": 符合,
        "期望结局": 期望,
        "策略": cert["策略"], "口径": cert["口径"],
        "核证": cert,
        "放量门": gate,
        "三器共用正式集": 共用集,
        "集版本": cert["集版本"], "票印": cert["票印"],
    }


# --------------------------------------------------------------------------- #
# 并发演练：冲突写 与 放量/核证读 并发，断言读不到中间正式集
# --------------------------------------------------------------------------- #
def 并发演练(体: dict | None = None) -> dict:
    体 = 体 or {}
    票号 = 体.get("票号") or 体.get("票")
    if not 票号:
        票号, ver = 新建一券()
    else:
        conn = 连接()
        try:
            t = conn.execute("SELECT 版本键 FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
            if not t:
                raise HTTPException(400, f"下发票不存在:{票号}")
            ver = t["版本键"]
        finally:
            conn.close()

    轮数 = int(体.get("轮数", 体.get("写入数", 12)))
    读并发 = int(体.get("读并发", 6))
    每者读 = int(体.get("每者读", 8))
    edge = "edge-竞"

    异常: list[str] = []
    中间态 = 0
    读次数 = 0

    def writer(i: int) -> str:
        try:
            # 交替 确认/偏差，制造同边缘反复冲突写。
            _post(票号, edge, "确认" if i % 2 == 0 else "偏差", ver)
            return "ok"
        except Exception as e:  # noqa: BLE001
            异常.append(f"写异常:{e}")
            return "err"

    def reader() -> None:
        nonlocal 中间态, 读次数
        for _ in range(每者读):
            try:
                c = 核证(票号)
                读次数 += 1
                # 任何一次读到的都必须是已物化、自洽的完整裁定，而非中间/混合态。
                if not c["valid"]:
                    中间态 += 1
                    异常.append("核证 valid=false:" + ";".join(c["问题"]))
                # 返回的哈希必须与其声明版本+集合自洽（撕裂读过不了这关）。
                if c["集版本"] and c["集哈希"] != _集哈希(
                    票号, c["集版本"], c["正式集"], c["裁定标记"]
                ):
                    中间态 += 1
                    异常.append("核证票印与正式集不自洽")

                # 同一快照内核证与放量门必须严格共用正式集/版本。
                both = 同门核放(票号)
                cc, gg = both["核证"], both["放量门"]
                if not both["valid"] or not cc["valid"]:
                    中间态 += 1
                    异常.append("同门核放 valid=false:" + ";".join(cc["问题"]))
                if cc["正式集"] != gg["正式集"] or cc["集版本"] != gg["集版本"]:
                    中间态 += 1
                    异常.append("放量门与裁定未在同一快照共用正式集")

                # 独立放量读也必须自洽（跨调用版本可因并发写推进，不比较版本）。
                g = 放量门(票号)
                if not g["valid"]:
                    中间态 += 1
                    异常.append("放量门 valid=false")
                if g["集版本"] and g["集哈希"] != _集哈希(
                    票号, g["集版本"], g["正式集"], g["裁定标记"]
                ):
                    中间态 += 1
                    异常.append("放量门票印与正式集不自洽")
            except Exception as e:  # noqa: BLE001
                异常.append(f"读异常:{e}")

    with ThreadPoolExecutor(max_workers=max(2, 读并发 + 4)) as pool:
        wfuts = [pool.submit(writer, i) for i in range(轮数)]
        rfuts = [pool.submit(reader) for _ in range(读并发)]
        for f in wfuts:
            f.result()
        for f in rfuts:
            f.result()

    final = 核证(票号)
    if not final["valid"]:
        异常.append("终态核证失败:" + ";".join(final["问题"]))

    return {
        "valid": not 异常 and 中间态 == 0 and final["valid"],
        "票号": 票号, "同票": 票号,
        "轮数": 轮数, "写入数": 轮数, "读并发": 读并发,
        "读次数": 读次数, "中间态读到": 中间态,
        "异常": 异常[:20],
        "终态": {
            "正式集": final["正式集"], "旁证数": final["旁证数"],
            "裁定标记": final["裁定标记"], "valid": final["valid"],
            "集版本": final["集版本"], "票印": final["票印"],
        },
    }


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _确保样例券() -> tuple[str, str]:
    """幂等确保 README 主链样例券存在，返回 (票号, 版本键)。"""
    票号 = "票-checkout.new_tax@3-gray10"
    版本键 = "checkout.new_tax@3"
    conn = 连接()
    try:
        t = conn.execute("SELECT 版本键 FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if t:
            return 票号, t["版本键"]
    finally:
        conn.close()
    from app.services import 入轨, 分桶, 下发
    入轨.入一批({"版本": [{
        "开关键": "checkout.new_tax", "版本号": 3, "版本键": 版本键,
    }]})
    分桶.写桶({"桶键": "checkout.new_tax-gray10", "开关键": "checkout.new_tax"})
    下发.下一票({"票号": 票号, "版本键": 版本键, "桶键": "checkout.new_tax-gray10"})
    return 票号, 版本键


def 装冲突夹具(体: dict) -> dict:
    if 体.get("策略") or 体.get("策略值"):
        设置策略(体)
    if 体.get("双序"):
        return 跑双序(体)

    DEMO = "票-checkout.new_tax@3-gray10"
    items = 体.get("回读") or []
    票号 = 体.get("票号") or (items[0].get("票号") if items else "") or DEMO

    # 若票不存在：样例券则幂等自建底座；其余票直接 400。保证夹具自包含。
    conn = 连接()
    try:
        exists = conn.execute(
            "SELECT 1 FROM 下发票 WHERE 票号=?", (票号,)
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        if 票号 == DEMO:
            _确保样例券()
        else:
            raise HTTPException(400, f"下发票不存在:{票号}")

    if not items:
        items = [
            {"票号": 票号, "边缘键": "edge-echo-conflict", "结果": "确认"},
            {"票号": 票号, "边缘键": "edge-echo-conflict", "结果": "偏差",
             "所见版本键": "ghost@nowhere"},
        ]
    for it in items:
        it.setdefault("票号", 票号)

    from app.services import 回读
    结果 = [回读.记回读(dict(x)) for x in items]
    cert = 核证(票号)
    return {
        "类型": "回声冲突", "票号": 票号, "结果": 结果,
        "核证": {
            "正式集": cert["正式集"], "旁证数": cert["旁证数"],
            "裁定标记": cert["裁定标记"], "valid": cert["valid"],
        },
    }
