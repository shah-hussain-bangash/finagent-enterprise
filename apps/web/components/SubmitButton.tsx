"use client";
// Disables itself while the server action runs, so a double click cannot submit twice and the user
// sees that nothing is confirmed until the server responds.
import { useFormStatus } from "react-dom";

export function SubmitButton({ children, danger }: { children: string; danger?: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending} aria-busy={pending} className={danger ? "danger" : undefined}>
      {pending ? "Working..." : children}
    </button>
  );
}
