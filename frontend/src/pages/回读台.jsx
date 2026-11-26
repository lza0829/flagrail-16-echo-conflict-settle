import React, { useEffect, useState } from "react";
import { get, post, postFixture } from "../api.js";

const DEMO票 = "票-checkout.new_tax@3-gray10";
const 卡 = {
  background: "var(--块)",
  border: "1px solid var(--线弱)",
  borderRadius: 4,
  padding: "0.9rem 1rem",
  margin: "0.8rem 0",
};
const 网格 = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "0.8rem" };
const 小标 = { color: "var(--淡)", fontSize: "0.78rem", letterSpacing: "0.06em", marginBottom: "0.35rem" };

function 徽章({ ok, yes, no }) {
  return <span className={"徽章 " + (ok ? "通" : "败")}>{ok ? yes : no}</span>;
}

function 集chips({ list }) {
  if (!list || !list.length) return <span className="空态" style={{ padding: "0.2rem 0.5rem", margin: 0 }}>∅ 空集</span>;
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {list.map((e) => (
        <code key={e} className="徽章 信" style={{ fontSize: "0.78rem" }}>{e}</code>
      ))}
    </span>
  );
}

export default function 回读台() {
  const [rows, setRows] = useState([]);
  const [概, set概] = useState(null);
  const [ticket, setTicket] = useState(DEMO票);
  const [cert, setCert] = useState(null);
  const [dual, setDual] = useState(null);
  const [gate, setGate] = useState(null);
  const [讯, set讯] = useState("");
  const [busy, setBusy] = useState(false);

  const 刷 = async () => {
    const [r, o] = await Promise.all([get("/回读证"), get("/回声冲突裁定")]);
    setRows(r);
    set概(o);
  };
  useEffect(() => { 刷().catch((e) => set讯(String(e))); }, []);

  const 包 = async (fn, okmsg) => {
    setBusy(true);
    set讯("");
    try {
      const out = await fn();
      if (okmsg) set讯(okmsg);
      await 刷();
      return out;
    } catch (e) {
      set讯("" + e);
    } finally {
      setBusy(false);
    }
  };

  const 切策略 = (s) => 包(async () => {
    const r = await post("/裁定策略", { 策略: s });
    if (cert) setCert(await post("/核证", { 票号: cert.票号 }));
    return r;
  }, `已物化裁定策略：${s}`);

  const 跑核证 = () => 包(async () => {
    const c = await post("/核证", { 票号: ticket });
    setCert(c);
    const g = await post("/放量门", { 票号: ticket });
    setGate(g);
    return c;
  }, "核证完成");

  const 跑双序 = () => 包(async () => {
    const d = await post("/双序自动化", {});
    setDual(d);
    setTicket(d.票号);
    setCert(d.核证);
    setGate(d.放量门);
    return d;
  }, "双序自动化完成");

  const strat = 概?.裁定标记 || cert?.裁定标记 || "末次覆盖";

  return (
    <div>
      <h1 className="题">回读台 · 回声冲突裁定</h1>
      <p className="述">
        同一边缘先后回声打架时，按物化的裁定策略稳定正式确认集：裁定结果、正式证、放量门共用同一「正式集」，
        回声全量留痕、冲突只进一次裁决。回读台逐行列出<b>裁定标记</b>与<b>旁证序号</b>。
      </p>

      <div className="行">
        <button onClick={() => 包(() => postFixture("回读样例"), "已灌基础回读样例").then(() => 刷())}>灌回读样例</button>
        <button className="信" disabled={busy}
          onClick={() => 包(() => postFixture("回声冲突样例"), "已灌回声冲突样例（确认→偏差 / 偏差→确认）").then(() => post("/核证", { 票号: DEMO票 }).then(setCert).catch(() => {}))}>
          灌回声冲突样例
        </button>
        <button className="信" disabled={busy} onClick={跑双序}>双序自动化</button>
        <button className="次" onClick={() => 刷().catch((e) => set讯(String(e)))}>刷新</button>
      </div>

      <div style={卡}>
        <div style={网格}>
          <div>
            <div style={小标}>裁定策略表（全局唯一口径 · 可切换，两套时序同口径）</div>
            <div className="行" style={{ marginBottom: 4 }}>
              <button
                className={strat === "末次覆盖" ? "信" : "次"}
                onClick={() => 切策略("末次覆盖")}>末次覆盖</button>
              <button
                className={strat === "首次锁定" ? "信" : "次"}
                onClick={() => 切策略("首次锁定")}>首次锁定</button>
            </div>
            <div className="讯" style={{ margin: 0 }}>
              当前裁定标记：<b>{strat}</b>
              {概?.口径 ? `　口径：${概.口径}` : ""}
            </div>
          </div>
          <div>
            <div style={小标}>核证 / 放量门同票</div>
            <div className="行" style={{ marginBottom: 4 }}>
              <input
                value={ticket}
                onChange={(e) => setTicket(e.target.value)}
                placeholder="票号"
                style={{ flex: "1 1 220px", padding: "0.5rem 0.6rem", border: "1px solid var(--线)", borderRadius: 2, font: "inherit" }}
              />
              <button onClick={跑核证}>核证本券</button>
            </div>
            <div className="讯" style={{ margin: 0 }}>冲突写与放量读同票；单快照读，禁止读中间正式集。</div>
          </div>
        </div>
      </div>

      {讯 && <p className="讯" data-testid="msg">{讯}</p>}

      {cert && (
        <div style={卡} data-testid="cert">
          <div style={网格}>
            <div>
              <div style={小标}>核证返回</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
                <徽章 ok={!!cert.valid} yes="valid: true" no="valid: false" />
                <span className="徽章 信">裁定标记：{cert.裁定标记}</span>
                <span className="徽章">旁证数：{cert.旁证数}</span>
                <span className="徽章">集版本 v{cert.集版本}</span>
              </div>
              <div style={小标}>正式集（裁定结果 / 正式证 / 放量门共用）</div>
              <集chips list={cert.正式集} />
              <div className="讯" style={{ marginTop: 8 }}>
                票印 <code>{cert.票印 || "—"}</code>
                {cert.问题?.length ? `　问题：${cert.问题.join("；")}` : ""}
              </div>
            </div>
            {gate && (
              <div data-testid="gate">
                <div style={小标}>放量门（读同一正式集）</div>
                <div style={{ marginBottom: 8 }}>
                  <徽章 ok={!!gate.放行} yes="放行 · 可放量" no="拦截 · 不放量" />
                </div>
                <div style={小标}>门内正式集</div>
                <集chips list={gate.正式集} />
                <div className="讯" style={{ marginTop: 8 }}>
                  同票 <code>{gate.同票}</code> · 缺缘 {gate.缺缘?.length ? gate.缺缘.join(",") : "无"}
                  <br />{gate.理由?.join("；")}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {dual && (
        <div style={卡} data-testid="dual">
          <div style={小标}>双序自动化结局 · 必须符合同一策略（{dual.裁定标记}）</div>
          <div style={网格}>
            {dual.序列?.map((s) => (
              <div key={s.时序} style={{ border: "1px solid var(--线弱)", borderRadius: 4, padding: "0.6rem 0.8rem", background: "rgba(255,255,255,.5)" }}
                data-testid={s.时序 === "确认→偏差" ? "seq-a" : "seq-b"}>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>
                  {s.时序}　<徽章 ok={s.符合策略} yes="符合策略" no="违背策略" />
                </div>
                <div className="讯" style={{ margin: "2px 0" }}>边缘 <code>{s.边缘键}</code></div>
                <div>冲突后仍含该边缘：<b>{String(s.冲突后仍含该边缘)}</b>（期望 {String(s.期望含缘)}）</div>
                <div className="讯" style={{ margin: "2px 0" }}>
                  旁证序号：{s.旁证序号?.length ? s.旁证序号.map((n) => `#${n}`).join(" ") : "无"}
                </div>
              </div>
            ))}
          </div>
          <div className="讯" style={{ marginTop: 8 }}>
            valid {String(dual.valid)} · 旁证数 {dual.旁证数} · 三器共用正式集 {String(dual.三器共用正式集)} · 票 <code>{dual.票号}</code>
          </div>
        </div>
      )}

      <h2 style={{ fontSize: "1.02rem", margin: "1.1rem 0 .4rem" }}>回读流水（全量历史 · 裁定标记 · 旁证序号）</h2>
      <table data-testid="readback-table">
        <thead>
          <tr>
            <th>旁证序号</th><th>裁定标记</th><th>证号</th><th>票号</th><th>边缘</th>
            <th>所见版本</th><th>结果</th><th>处置</th><th>在正式集</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.序号 ?? r.证号}>
              <td>{r.旁证序号 != null ? <span className="徽章 信">#{r.旁证序号}</span> : "—"}</td>
              <td><span className="徽章 信">{r.裁定标记 || "未裁定"}</span></td>
              <td className="mono">{r.证号}</td>
              <td className="mono">{r.票号}</td>
              <td>{r.边缘键}</td>
              <td className="mono">{r.所见版本键}</td>
              <td><span className={"徽章 " + (r.结果 === "确认" ? "通" : "败")}>{r.结果}</span></td>
              <td>{r.处置 || "—"}{r.旁证类别 ? <div className="讯" style={{ margin: 0 }}>{r.旁证类别}</div> : null}</td>
              <td>{r.入正式集 == null ? "—" : <徽章 ok={!!r.入正式集} yes="含" no="不含" />}</td>
            </tr>
          ))}
          {!rows.length && (
            <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--淡)", padding: "1.2rem" }}>
              尚无回读流水（可点「灌回声冲突样例」或「双序自动化」）
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
