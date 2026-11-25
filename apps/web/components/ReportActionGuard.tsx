"use client";

import { createContext, useState, type ReactNode } from "react";

export const ReportActionLock = createContext<() => void>(() => {});

export function ReportActionGuard({ children }: { children: ReactNode }) {
  const [locked, setLocked] = useState(false);
  return <ReportActionLock.Provider value={() => setLocked(true)}>
    <div onSubmitCapture={(event) => { if (locked) { event.preventDefault(); event.stopPropagation(); } }}>
      {locked && <p role="status" className="warn">Report actions are paused. Check the recorded report before continuing.</p>}
      {children}
    </div>
  </ReportActionLock.Provider>;
}
