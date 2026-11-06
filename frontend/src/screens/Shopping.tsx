import { useState } from "react";
import { Household } from "../api";
import Pantry from "./Pantry";
import ShoppingList from "./ShoppingList";
import Receipts from "./Receipts";
import Prices from "./Prices";

export default function Shopping({ household, initial }: { household: Household; initial: "list" | "pantry" | "receipts" | "prices" }) {
  const [view, setView] = useState(initial);
  return (
    <>
      <h1>{{ list: "Shopping list", pantry: "Pantry", receipts: "Receipts", prices: "Prices" }[view]}</h1>
      <div className="seg" role="group" aria-label="Show" style={{ marginBottom: 16, marginTop: -6 }}>
        <button aria-pressed={view === "list"} onClick={() => { setView("list"); history.replaceState(null, "", "#/list"); }}>List</button>
        <button aria-pressed={view === "pantry"} onClick={() => { setView("pantry"); history.replaceState(null, "", "#/pantry"); }}>Pantry</button>
        <button aria-pressed={view === "receipts"} onClick={() => { setView("receipts"); history.replaceState(null, "", "#/receipts"); }}>Receipts</button>
        <button aria-pressed={view === "prices"} onClick={() => { setView("prices"); history.replaceState(null, "", "#/prices"); }}>Prices</button>
      </div>
      {view === "list" ? <ShoppingList household={household} /> : view === "pantry" ? <Pantry household={household} embedded />
        : view === "receipts" ? <Receipts household={household} /> : <Prices household={household} />}
    </>
  );
}
