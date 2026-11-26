from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 开关版本 (
  版本键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本号 INTEGER NOT NULL,
  载荷 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '已入轨',
  入轨时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 分桶规 (
  桶键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  桶类型 TEXT NOT NULL,
  规则 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '生效',
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 下发票 (
  票号 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本键 TEXT NOT NULL,
  桶键 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '待下发',
  下发时 TEXT NOT NULL DEFAULT '',
  世代 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 回读证 (
  证号 TEXT PRIMARY KEY,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  所见版本键 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL,
  回读时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 命运账 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  对象类型 TEXT NOT NULL,
  对象键 TEXT NOT NULL,
  命运 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 轨贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  版本数 INTEGER NOT NULL,
  下发数 INTEGER NOT NULL,
  回读数 INTEGER NOT NULL,
  命运数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 空桩 (
  名 TEXT PRIMARY KEY,
  状态 TEXT NOT NULL DEFAULT '空桩'
);

-- ===== 回声冲突裁定（邻题扩展） =====
-- 物化裁定策略表：同一边缘回声冲突按哪一口径裁定，全局唯一口径，双序共用。
CREATE TABLE IF NOT EXISTS 裁定策略 (
  作用域 TEXT PRIMARY KEY,
  策略 TEXT NOT NULL,
  策略值 TEXT NOT NULL,
  裁定标记 TEXT NOT NULL,
  口径 TEXT NOT NULL DEFAULT '',
  更新时 TEXT NOT NULL
);
-- 不可变回声流水：每一次回读都追加一行，禁止只留最后一行。
CREATE TABLE IF NOT EXISTS 回声流水 (
  序号 INTEGER PRIMARY KEY AUTOINCREMENT,
  证号 TEXT NOT NULL,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  所见版本键 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL,
  回读时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_回声流水_票缘 ON 回声流水(票号, 边缘键, 序号);
-- 旁证历史：未作为当前正式裁定依据的回声，按策略口径物化并给旁证序号。
-- 旁证序号在 (策略, 票号) 内连续；同一流水在同一策略下至多成为一条旁证。
CREATE TABLE IF NOT EXISTS 旁证历史 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  旁证序号 INTEGER NOT NULL,
  策略值 TEXT NOT NULL,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  流水序号 INTEGER NOT NULL,
  证号 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL DEFAULT '',
  所见版本键 TEXT NOT NULL DEFAULT '',
  旁证类别 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_旁证_策略流水 ON 旁证历史(策略值, 流水序号);
CREATE INDEX IF NOT EXISTS idx_旁证_票 ON 旁证历史(策略值, 票号, 旁证序号);
-- 正式集投影：裁定结果 / 正式证 / 放量门唯一共用的正式集（每票一行，原子更新）。
CREATE TABLE IF NOT EXISTS 正式集 (
  票号 TEXT PRIMARY KEY,
  正式集 TEXT NOT NULL DEFAULT '[]',
  集版本 INTEGER NOT NULL DEFAULT 1,
  集哈希 TEXT NOT NULL DEFAULT '',
  策略值 TEXT NOT NULL,
  裁定标记 TEXT NOT NULL,
  旁证数 INTEGER NOT NULL DEFAULT 0,
  冲突数 INTEGER NOT NULL DEFAULT 0,
  回声数 INTEGER NOT NULL DEFAULT 0,
  有效 INTEGER NOT NULL DEFAULT 1,
  更新时 TEXT NOT NULL DEFAULT ''
);
-- 正式证：正式集的规范化物化（有效行必须与 正式集.正式集 完全一致）。
CREATE TABLE IF NOT EXISTS 正式确认证 (
  正式证号 TEXT PRIMARY KEY,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '有效',
  集版本 INTEGER NOT NULL DEFAULT 1,
  策略值 TEXT NOT NULL DEFAULT '',
  裁定时 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_正式证_票 ON 正式确认证(票号, 状态);

-- 题目原词「物化裁定策略表」的具名只读视图，指向唯一物理策略表 裁定策略。
CREATE VIEW IF NOT EXISTS 裁定策略表 AS
  SELECT 作用域, 策略, 策略值, 裁定标记, 口径, 更新时 FROM 裁定策略;
"""

# 默认裁定口径：二选一的「末次覆盖」。可在运行时整体切到「首次锁定」，
# 两套口径都完整实现并可区分（见 services.回声裁定）。
默认策略 = "末次覆盖"

策略口径 = {
    "末次覆盖": "以末次覆盖为准——后到偏差摘掉正式确认，放量按新集",
    "首次锁定": "以首次锁定为准——首次确认后同边缘偏差只进旁证，正式确认保留",
}


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    if conn.execute("SELECT COUNT(*) AS c FROM 站规").fetchone()["c"] == 0:
        for k, v in {
            "站名": "灰度轨",
            "领域": "基础设施",
            "子方向": "中间件",
            "默认灰度比": "10",
            "回读齐套策略": "全边缘确认才可晋升",
            "骨架": "开关版本入轨→分桶灰度下发→回读确认→失效/回滚互证",
        }.items():
            conn.execute("INSERT INTO 站规(键, 值) VALUES (?, ?)", (k, v))
    for name in [
        "分桶比例冲突", "名单与百分比互斥", "回读超时旁置", "半截下发回滚",
        "双版本同键并发", "回读偏差拒升全量", "环境桶隔离", "失效后幽灵回读",
        "下发票幂等键", "回滚窗互斥", "变体载荷契约", "边缘时钟漂移",
        "分桶重切", "全量晋升闸", "影子轨对照", "配置世代戳",
        "回读仲裁策略", "失效写闸", "跨环境串扰", "轨贯验三联",
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO 空桩(名, 状态) VALUES (?, '空桩')", (name,)
        )
    # 本桩已物化：README 目录名「回声冲突裁定」与旧桩名「回读仲裁策略」都点亮。
    for name in ["回声冲突裁定", "回读仲裁策略"]:
        conn.execute(
            "INSERT INTO 空桩(名, 状态) VALUES (?, '已物化') "
            "ON CONFLICT(名) DO UPDATE SET 状态='已物化'",
            (name,),
        )
    # 物化默认裁定策略行（不覆盖已存在的口径，尊重运行时切换）。
    conn.execute(
        """INSERT OR IGNORE INTO 裁定策略(作用域, 策略, 策略值, 裁定标记, 口径, 更新时)
           VALUES ('回声冲突', ?, ?, ?, ?, ?)""",
        (默认策略, 默认策略, 默认策略, 策略口径[默认策略], 此刻()),
    )
    conn.commit()
    conn.close()
