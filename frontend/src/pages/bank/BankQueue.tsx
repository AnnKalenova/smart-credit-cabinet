import { useQuery } from "@tanstack/react-query";
import { MessageSquareText, Search } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { BankRow } from "../../api/types";
import { Empty, Grade, Loader, bankTone } from "../../components/ui";
import { dateTime, money, relativeDue } from "../../lib/format";

const SCOPES = [
  { key: "queue", label: "Ручное рассмотрение" },
  { key: "active", label: "В работе" },
  { key: "mine", label: "Мои" },
  { key: "all", label: "Все" },
] as const;

export function BankQueue() {
  const nav = useNavigate();
  const [scope, setScope] = useState<(typeof SCOPES)[number]["key"]>("queue");
  const [q, setQ] = useState("");
  const rows = useQuery({
    queryKey: ["bank-apps", scope, q],
    queryFn: () => api<BankRow[]>(`/api/v1/bank/applications?scope=${scope}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    refetchInterval: 4000,
  });
  const queueCount = useQuery({
    queryKey: ["bank-apps", "queue", ""],
    queryFn: () => api<BankRow[]>(`/api/v1/bank/applications?scope=queue`),
    refetchInterval: 4000,
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Заявки</h1>
          <p className="muted">Большую часть заявок конвейер решает сам. Сюда попадают те, где нужна экспертиза специалиста.</p>
        </div>
        <div className="input-group" style={{ width: 300 }}>
          <input className="input" placeholder="Номер, компания или ИНН" maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск" />
          <span className="suffix">
            <Search size={16} />
          </span>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {SCOPES.map((s) => (
          <button key={s.key} role="tab" aria-selected={scope === s.key} className={scope === s.key ? "on" : ""} onClick={() => setScope(s.key)}>
            {s.label}
            {s.key === "queue" && (queueCount.data?.length ?? 0) > 0 && <span className="count">{queueCount.data?.length}</span>}
          </button>
        ))}
      </div>
      <section className="panel panel-table">
        {rows.isLoading && <Loader />}
        {rows.data && rows.data.length === 0 && (
          <Empty title={scope === "queue" ? "Очередь пуста" : "Заявок нет"}>
            <p className="small">
              {scope === "queue" ? "Все поступившие заявки решены автоматически. Новые появятся здесь сами." : "Попробуйте другой фильтр."}
            </p>
          </Empty>
        )}
        {rows.data && rows.data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Заявка</th>
                  <th>Клиент</th>
                  <th>Продукт</th>
                  <th className="r">Сумма</th>
                  <th>Статус</th>
                  <th>Рейтинг</th>
                  <th>{scope === "queue" ? "Срок решения" : "Изменена"}</th>
                  <th>Специалист</th>
                </tr>
              </thead>
              <tbody>
                {rows.data.map((r) => {
                  const due = relativeDue(r.review_due_at);
                  return (
                    <tr key={r.id} className="clickable" onClick={() => nav(`/b/applications/${r.id}`)}>
                      <td className="nowrap">
                        <b>{r.number}</b>
                        <div className="xs faint">{dateTime(r.submitted_at)}</div>
                      </td>
                      <td>
                        {r.company_name}
                        <div className="xs faint">ИНН {r.company_inn}</div>
                        {r.awaiting_reply && (
                          <div className="xs row" style={{ gap: 4, marginTop: 2, color: "var(--info)" }}>
                            <MessageSquareText size={13} /> Клиент ждёт ответа на сообщение
                          </div>
                        )}
                        {r.triggers.length > 0 && (
                          <div className="xs trigger" style={{ marginTop: 2 }}>
                            {r.triggers.slice(0, 2).join("; ")}
                            {r.triggers.length > 2 ? ` и ещё ${r.triggers.length - 2}` : ""}
                          </div>
                        )}
                      </td>
                      <td>
                        {r.product_name}
                        <div className="xs faint">{r.term_months} мес.</div>
                      </td>
                      <td className="r nowrap">{money(r.amount)}</td>
                      <td>
                        <span className={`chip ${bankTone(r.status)}`}>{r.status_label}</span>
                        {r.stage_detail && !["ISSUED", "DECLINED"].includes(r.status) && <div className="xs faint" style={{ marginTop: 4, maxWidth: 240 }}>{r.stage_detail}</div>}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          <Grade grade={r.grade} />
                          {r.score && <span className="xs faint">{r.score}</span>}
                        </div>
                      </td>
                      <td className="nowrap">
                        {scope === "queue" ? <span className={due.overdue ? "sla-bad" : ""}>{due.text}</span> : dateTime(r.status_changed_at)}
                      </td>
                      <td>{r.assignee ?? <span className="faint">не назначен</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
