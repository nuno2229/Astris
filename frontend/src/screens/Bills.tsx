import { useState } from "react";
import { Household } from "../api";
import RecurringScreen from "./Recurring";
import Subscriptions from "./Subscriptions";

export default function Bills({ household, currency, initial }: { household: Household; currency: string; initial?: string }) {
  const [view, setView] = useState<"subs" | "regular">(initial === "regular" ? "regular" : "subs");
  return (
    <>
      <h1>Bills</h1>
      <div className="seg" role="group" aria-label="Show" style={{ marginBottom: 18 }}>
        <button aria-pressed={view === "subs"} onClick={() => setView("subs")}>Subscriptions</button>
        <button aria-pressed={view === "regular"} onClick={() => setView("regular")}>All regular payments</button>
      </div>
      {view === "subs" ? <Subscriptions household={household} currency={currency} /> : <RecurringScreen household={household} embedded />}
    </>
  );
}
