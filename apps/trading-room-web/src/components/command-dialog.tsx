"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

type CommandDialogProps = Readonly<{
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  reasonLabel?: string;
  reasonRequired?: boolean;
  danger?: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}>;

export function CommandDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "취소",
  reasonLabel,
  reasonRequired = false,
  danger = false,
  onCancel,
  onConfirm,
}: CommandDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const [validationError, setValidationError] = useState<string>();

  useEffect(() => {
    const element = dialog.current;
    if (element === null) return;
    if (open && !element.open) {
      setReason("");
      setValidationError(undefined);
      element.showModal();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  function cancel() {
    dialog.current?.close();
    onCancel();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedReason = reason.trim();
    if (reasonRequired && normalizedReason.length === 0) {
      setValidationError("사유를 입력하세요.");
      return;
    }
    dialog.current?.close();
    onConfirm(normalizedReason);
  }

  return (
    <dialog
      ref={dialog}
      className="command-dialog"
      aria-labelledby="command-dialog-title"
      aria-describedby="command-dialog-description"
      onCancel={(event) => {
        event.preventDefault();
        cancel();
      }}
    >
      <form className="form-grid" noValidate onSubmit={submit}>
        <h2 id="command-dialog-title">{title}</h2>
        <p id="command-dialog-description">{description}</p>
        {reasonLabel !== undefined && (
          <label>
            {reasonLabel}
            <textarea
              autoFocus
              aria-required={reasonRequired}
              aria-invalid={validationError !== undefined}
              value={reason}
              onChange={(event) => {
                setReason(event.currentTarget.value);
                if (event.currentTarget.value.trim().length > 0) setValidationError(undefined);
              }}
            />
          </label>
        )}
        {validationError !== undefined && <p className="command-result error" role="alert">{validationError}</p>}
        <div className="actions">
          <button type="button" className="secondary" onClick={cancel}>{cancelLabel}</button>
          <button type="submit" className={danger ? "danger" : undefined}>{confirmLabel}</button>
        </div>
      </form>
    </dialog>
  );
}
