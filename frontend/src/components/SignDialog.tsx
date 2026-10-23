import { FileSignature, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SignChallenge } from "../api/types";
import { errorText } from "../lib/toast";
import { Modal } from "./ui";

/** Простая электронная подпись кодом подтверждения внутри кабинета.
 *  На демо-стенде сервер возвращает код вместе с запросом, и окно показывает его рядом с полем ввода. */
export function SignDialog({
  appId,
  purpose,
  title,
  onClose,
  onConfirm,
}: {
  appId: string;
  purpose: "CONSENTS" | "CONTRACT";
  title: string;
  onClose: () => void;
  onConfirm: (challengeId: string, code: string) => Promise<void>;
}) {
  const [challenge, setChallenge] = useState<SignChallenge | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const requestCode = async () => {
    setError(null);
    setCode("");
    try {
      setChallenge(await api<SignChallenge>(`/api/v1/applications/${appId}/sign-challenges`, { method: "POST", json: { purpose } }));
    } catch (e) {
      setError(errorText(e));
    }
  };

  useEffect(() => {
    void requestCode();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confirm = async () => {
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(challenge.challenge_id, code);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn-signal" disabled={!challenge || code.length < 6 || busy} onClick={confirm}>
            <FileSignature size={18} /> Подписать
          </button>
        </>
      }
    >
      <div className="stack">
        {challenge && (
          <div>
            <p className="small muted">Вы подписываете простой электронной подписью:</p>
            <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {challenge.documents.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        )}
        {challenge?.demo_code && (
          <div className="demo-code">
            <span className="row" style={{ gap: 10 }}>
              <KeyRound size={18} className="faint" />
              <span>
                <span className="xs faint" style={{ display: "block" }}>
                  Ваш код
                </span>
                <b>{challenge.demo_code}</b>
              </span>
            </span>
            <button className="btn btn-ghost btn-s" onClick={() => setCode(challenge.demo_code ?? "")}>
              Вставить код
            </button>
          </div>
        )}
        <div className="field">
          <label htmlFor="sign-code">Код подтверждения</label>
          <input
            id="sign-code"
            className="input code-input"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && code.length === 6 && confirm()}
          />
          <span className="field-hint">
            Код действует 5 минут.{" "}
            <button className="btn-link" onClick={requestCode}>
              Получить новый код
            </button>
          </span>
        </div>
        {error && <div className="callout bad">{error}</div>}
      </div>
    </Modal>
  );
}
