import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { Product, SettingRow } from "../../api/types";
import { Field, LoadError, Loader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { dateTime, money, pct } from "../../lib/format";
import { useSingleFlight } from "../../lib/once";
import { useToast } from "../../lib/toast";

const POLICY = [
  ["Долговая нагрузка", "Платежи по всем кредитам — не более 40% месячных поступлений на счёт. Учитываются кредиты в других банках (по БКИ) и кредиты нашего банка, в том числе на подписании"],
  ["Лимит по рейтингу", "A — 100%, B — 90%, C — 75%, D — 60% от расчётного; E — отказ"],
  ["Надбавка за риск", "A — 0, B — +1, C — +2,5, D — +4 п.п. к ставке; итог округляется до 0,1 п.п."],
  ["Скидки", "Залог −1 п.п., поручительство собственника −0,5 п.п., клиент банка более 2 лет −0,5 п.п."],
  ["Стоп-факторы", "Ликвидация или банкротство, просрочка свыше 90 дней, бизнесу меньше 3 месяцев, рейтинг E"],
  ["Ручное рассмотрение", "Сумма выше лимита автоматического решения, рейтинг D, налоговый долг свыше 300\u00a0тыс.\u00a0₽, смена руководителя за 6 месяцев, признаки риска ФНС, просрочка 31–90 дней, выручка по выписке другого банка более чем вдвое выше поступлений на счёт, отказ кредитного специалиста по заявке компании за последние 90 дней"],
  ["Уточнение выручки", "Если заявленная выручка больше годовых поступлений на счёт более чем в 1,6 раза, банк просит выписку из другого банка. Подтверждённая выручка учитывается с дисконтом 20% и не выше двукратных поступлений на счёт"],
  ["Другие продукты", "Встречное предложение по другому продукту — только если для него не нужны залог и документы, которых нет в заявке"],
];

function KeyRate({ row, editable }: { row: SettingRow; editable: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [value, setValue] = useState(String(row.value).replace(".", ","));
  const once = useSingleFlight();
  const text = value.replace(/[\s%]/g, "").replace(",", ".");
  const valid = /^\d+(\.\d+)?$/.test(text);
  const save = useMutation({
    mutationFn: () => api(`/api/v1/bank/settings/key_rate`, { method: "PUT", json: { value: Number(text) } }),
    onSuccess: () => {
      toast.ok("Ключевая ставка обновлена — новые предложения будут рассчитаны по ней");
      void qc.invalidateQueries({ queryKey: ["settings"] });
      void qc.invalidateQueries({ queryKey: ["dict"] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="row-wrap" style={{ alignItems: "flex-end", gap: 12 }}>
      <div style={{ width: 160 }}>
        <Field label="Ставка, % годовых" error={editable && !valid ? "Укажите число, например 16,5" : undefined}>
          <input className="input input-money" value={value} readOnly={!editable} onChange={(e) => setValue(e.target.value)} inputMode="decimal" />
        </Field>
      </div>
      {editable && (
        <button
          className="btn btn-primary"
          disabled={save.isPending || !valid}
          onClick={() => once(() => save.mutateAsync().catch(() => undefined))}
        >
          Сохранить
        </button>
      )}
      <span className="small muted">
        Изменено: {dateTime(row.updated_at)}
        {row.updated_by ? `, ${row.updated_by}` : ""}
      </span>
    </div>
  );
}

export function BankSettings() {
  const { me } = useAuth();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingRow[]>("/api/v1/bank/settings") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => api<Product[]>("/api/v1/products") });
  const editable = me?.user.role === "RISK_MANAGER";
  const keyRate = settings.data?.find((s) => s.key === "key_rate");
  const note = settings.data?.find((s) => s.key === "key_rate_note");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Параметры кредитной политики</h1>
          <p className="muted">
            {editable ? "Вы можете менять ключевую ставку — она сразу учитывается в новых расчётах." : "Менять параметры может риск-менеджер."}
          </p>
        </div>
      </div>
      <div className="stack">
        <section className="panel">
          <h2>Ключевая ставка</h2>
          <div style={{ marginTop: 14 }}>
            {keyRate ? (
              <KeyRate row={keyRate} editable={editable} />
            ) : settings.isError ? (
              <LoadError title="Не удалось загрузить ставку" onRetry={() => void settings.refetch()} />
            ) : (
              <Loader />
            )}
          </div>
          {note && <p className="small muted" style={{ marginTop: 10 }}>{String(note.value)}</p>}
        </section>
        <section className="panel">
          <h2>Продукты</h2>
          <p className="small muted" style={{ marginTop: 6 }}>Ставка = ключевая ставка + надбавка продукта + надбавка за риск − скидки.</p>
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Продукт</th>
                  <th className="r">Сумма</th>
                  <th className="r">Срок, мес.</th>
                  <th className="r">Надбавка</th>
                  <th className="r">Автоматически до</th>
                  <th>Залог</th>
                </tr>
              </thead>
              <tbody>
                {products.isError && !products.data && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Не удалось загрузить продукты.{" "}
                      <button className="btn-link" onClick={() => void products.refetch()}>
                        Повторить
                      </button>
                    </td>
                  </tr>
                )}
                {products.data?.map((p) => (
                  <tr key={p.code}>
                    <td>
                      <b>{p.name}</b>
                      <div className="xs faint">{p.tagline}</div>
                    </td>
                    <td className="r nowrap">
                      {money(p.min_amount)} – {money(p.max_amount)}
                    </td>
                    <td className="r">{p.min_term === p.max_term ? p.min_term : `${p.min_term}–${p.max_term}`}</td>
                    <td className="r">+{pct(p.base_spread)}</td>
                    <td className="r nowrap">{money(p.auto_decision_limit)}</td>
                    <td>{p.collateral === "REQUIRED" ? "обязателен" : p.collateral === "OPTIONAL" ? "по желанию" : "не нужен"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="panel">
          <h2>Правила политики credit-policy-1.1</h2>
          <dl className="kv" style={{ marginTop: 12 }}>
            {POLICY.map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </>
  );
}
