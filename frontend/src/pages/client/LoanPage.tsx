import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Loan } from "../../api/types";
import { Loader } from "../../components/ui";
import { date, money, pct } from "../../lib/format";
import { ScheduleTable } from "./OfferPanels";

export function LoanPage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["loan", id], queryFn: () => api<Loan>(`/api/v1/loans/${id}`) });
  if (q.isLoading) return <Loader />;
  if (!q.data) return <div className="panel">Кредит не найден</div>;
  const l = q.data;
  const revolving = l.repayment_type === "REVOLVING";
  const totalInterest = (l.schedule ?? []).reduce((s, r) => s + r.interest, 0);
  return (
    <>
      <div className="crumbs">
        <Link to="/c">Главная</Link>
        <span>/</span>
        <span>Договор {l.contract_number}</span>
      </div>
      <div className="page-head">
        <div>
          <h1>
            {l.product_name}, {money(l.amount)}
          </h1>
          <p className="muted">
            Договор {l.contract_number} от {date(l.issued_at)}, ссудный счёт {l.loan_account}.{" "}
            <Link to={`/c/applications/${l.application_id}`}>Заявка №&nbsp;{l.application_number}</Link>
          </p>
        </div>
      </div>
      <div className="kpis" style={{ marginBottom: 20 }}>
        <div className="kpi">
          <div className="l">{revolving ? "Лимит" : "Сумма кредита"}</div>
          <div className="v">{money(l.amount)}</div>
        </div>
        <div className="kpi">
          <div className="l">Ставка</div>
          <div className="v">{pct(l.rate)}</div>
        </div>
        <div className="kpi">
          <div className="l">{revolving ? "Проценты в месяц при полном использовании" : "Платёж в месяц"}</div>
          <div className="v">{money(l.monthly_payment)}</div>
        </div>
        <div className="kpi">
          <div className="l">{revolving ? "Проценты за год при полном использовании" : "Проценты за весь срок"}</div>
          <div className="v">{money(totalInterest)}</div>
        </div>
      </div>
      <section className="panel">
        <div className="panel-head">
          <h2>График платежей</h2>
          <span className="small muted">Досрочное погашение — без комиссии, в любой день</span>
        </div>
        {revolving && (
          <p className="small muted" style={{ marginBottom: 12 }}>
            По овердрафту проценты начисляются только на использованную сумму. Ниже — пример, если лимит использован полностью весь срок.
          </p>
        )}
        <ScheduleTable rows={l.schedule ?? []} revolving={revolving} />
      </section>
    </>
  );
}
