import { useState } from "react";
import { Household } from "../api";
import Pantry from "./Pantry";
import ShoppingList from "./ShoppingList";
import Receipts from "./Receipts";

export default function Shopping({ household, initial }: { household: Household; initial: "list" | "pantry" | "receipts" }) {
  const [view, setView] = useState(initial);
  return (
    <>
      <h1>{view === "list" ? "Shopping list" : view === "pantry" ? "Pantry" : "Receipts"}</h1>
      <div className="seg" role="group" aria-label="Show" style={{ marginBottom: 16, marginTop: -6 }}>
        <button aria-pressed={view === "list"} onClick={() => { setView("list"); history.replaceState(null, "", "#/list"); }}>List</button>
        <button aria-pressed={view === "pantry"} onClick={() => { setView("pantry"); history.replaceState(null, "", "#/pantry"); }}>Pantry</button>
        <button aria-pressed={view === "receipts"} onClick={() => { setView("receipts"); history.replaceState(null, "", "#/receipts"); }}>Receipts</button>
      </div>
      {view === "list" ? <ShoppingList household={household} /> : view === "pantry" ? <Pantry household={household} embedded /> : <Receipts household={household} />}
    </>
  );
}
