import { requireSession } from "@/lib/session";
import Workspace from "./Workspace";
import "./workspace.css";

export const metadata = { title: "Workspace preview | FinAgent Enterprise" };

export default async function WorkspacePage() {
  await requireSession();
  return <Workspace />;
}
