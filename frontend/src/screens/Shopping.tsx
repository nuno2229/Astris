import { useState } from "react";
import { Household } from "../api";
import Pantry from "./Pantry";
import ShoppingList from "./ShoppingList";

export default function Shopping({ household, initial }: { household: Household; initial: "list" | "pantry" }) {
  const [view, setView] = useState(initial);
  return (
    <>
      <h1>{view === "list" ? "Shopping list" : "Pantry"}</h1>
      <div className="seg" role="group" aria-label="Show" style={{ marginBottom: 16, marginTop: -6 }}>
        <button aria-pressed={view === "list"} onClick={() => { setView("list"); history.replaceState(null, "", "#/list"); }}>List</button>
        <button aria-pressed={view === "pantry"} onClick={() => { setView("pantry"); history.replaceState(null, "", "#/pantry"); }}>Pantry</button>
      </div>
      {view === "list" ? <ShoppingList household={household} /> : <Pantry household={household} embedded />}
    </>
  );
}
