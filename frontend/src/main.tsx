import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// No StrictMode: its double mount in development creates and removes a MapLibre map straight away, and the second
// map's style then never finished loading (seen with MapLibre 6 in `npm run dev`; production builds were fine).
createRoot(document.getElementById("root")!).render(<App />);
