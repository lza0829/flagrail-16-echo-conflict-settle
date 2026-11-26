import React, { useEffect, useState } from "react";
import { get, post } from "../api.js";

const DEMO票 = "票-checkout.new_tax@3-gray10";
const 卡 = {
  background: "var(--块)",
  border: "1px solid var(--线弱)",
  borderRadius: 4,
  padding: "1rem 1.1rem",
  margin: "0.9rem 0",
};

function 集({ list }) {
  if (!list || !list.length) return <span className="徽章 败">∅ 空集</span>;
  return list.map((e) => <code key={e} className="徽章 信" style={{ marginRight: 4 }}>{e}</code>);
}

export default function 放量门() {
  const [ticket, setTicket] = useState(DEMO票);
  const [roster, setRoster] = useState("");
  const [g, setG] = useState(null);
  const [dual, setDual] = useState(null);
  const [讯, set讯] = useState("");
  const [busy, setBusy] = useState(false);

  const 读门 = async (t = ticket, 落账 = true) => {
    const body = { 票号: t, 落账 };
    if (roster.trim()) body.边缘名册 = roster.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const out = await post("/放量门", body);
    setG(out);
    return out;
  };

  const 双序并过门 = async () => {
    setBusy(true);
    set讯("");
    try {
      const d = await post("/双序自动化", {});
      setDual(d);
      setTicket(d.票号);
      setRoster("");
      const out = await post("/放量门", { 票号: d.票号, 落账: true });
      setG(out);
      set讯("已在全新双序券上读门（冲突写与放量读同票同快照）");
    } catch (e) {
      set讯(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1 className="题">放量门</h1>
      <p className="述">
        放量门不另立集合：与<b>裁定结果</b>、<b>正式证</b>共读「正式集」投影，同票、同集版本/票印。
        冲突写与放量读之间不会暴露中间正式集；核证 valid 且正式集覆盖应确认名册才放行。
      </p>

      <div className="行">
        <input value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="票号"
          style={{ flex: "1 1 260px", padding: "0.55rem 0.7rem", border: "1px solid var(--线)", borderRadius: 2, font: "inherit" }} />
        <input value={roster} onChange={(e) => setRoster(e.target.value)}
          placeholder="应确认边缘名册（逗号分隔，留空=该票全部回声边缘）"
          style={{ flex: "2 1 320px", padding: "0.55rem 0.7rem", border: "1px solid var(--线)", borderRadius: 2, font: "inherit" }} />
        <button className="信" disabled={busy} onClick={() => 读门().catch((e) => set讯(String(e)))}>读放量门</button>
        <button className="次" disabled={busy} onClick={双序并过门}>双序自动化券并过门</button>
      </div>
      {讯 && <p className="讯">{讯}</p>}

      {!g && <div className="空态">尚未读门。输入票号后点「读放量门」，或先用「双序自动化券并过门」。</div>}

      {g && (
        <div style={卡} data-testid="gate-panel">
          <div className="行" style={{ marginBottom: 8 }}>
            <span className={"徽章 " + (g.放行 ? "通" : "败")} style={{ fontSize: ".9rem" }}>
              {g.放行 ? "放行 · 可放量" : "拦截 · 不放量"}
            </span>
            <span className={"徽章 " + (g.valid ? "通" : "败")}>valid: {String(g.valid)}</span>
            <span className="徽章 信">裁定标记：{g.裁定标记}</span>
            <span className="徽章">旁证数：{g.旁证数}</span>
          </div>
          <div className="讯">同票 <code>{g.同票}</code> · 集版本 v{g.集版本} · 票印 <code>{g.票印}</code></div>
          <div style={{ marginTop: 8 }}><b>门读正式集：</b> <集 list={g.正式集} /></div>
          <div style={{ marginTop: 6 }}><b>应确认：</b>{(g.应确认边缘 || []).join(", ") || "（空）"}</div>
          <div style={{ marginTop: 6 }}><b>缺缘：</b>{(g.缺缘 || []).join(", ") || "无"}</div>
          <div className="讯" style={{ marginTop: 8 }}>{(g.理由 || []).join("；")}</div>
        </div>
      )}

      {dual && (
        <div style={卡}>
          <div style={{ marginBottom: 6 }}><b>最近双序结局（{dual.裁定标记}）</b></div>
          {(dual.序列 || []).map((s) => (
            <div key={s.时序} className="讯" style={{ margin: "2px 0" }}>
              {s.时序} → 仍含该边缘 <b>{String(s.冲突后仍含该边缘)}</b>，旁证序号 {s.旁证序号?.length ? s.旁证序号.map((n) => `#${n}`).join(" ") : "无"}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
