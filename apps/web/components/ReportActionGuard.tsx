"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

export const ReportActionLock = createContext<() => void>(() => {});
const ReportActionsDisabled = createContext(false);

export function ReportActionArea({ children }: { children: ReactNode }) {
  const disabled = useContext(ReportActionsDisabled);
  return <fieldset disabled={disabled} aria-label="Report actions" style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>{children}</fieldset>;
}

export function ReportActionGuard({ children }: { children: ReactNode }) {
  const [locked, setLocked] = useState(false);
  return <ReportActionLock.Provider value={() => setLocked(true)}>
    <ReportActionsDisabled.Provider value={locked}>
    <div onSubmitCapture={(event) => { if (locked) { event.preventDefault(); event.stopPropagation(); } }}>
      {locked && <p role="status" className="warn">Report actions are paused. Check the recorded report before continuing.</p>}
      {children}
    </div>
    </ReportActionsDisabled.Provider>
  </ReportActionLock.Provider>;
}
