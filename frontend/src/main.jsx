import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

// áp theme đã chọn trước khi vẽ để không nháy màu
const t = localStorage.getItem("fraudlens_theme") || "system";
if (t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
  document.documentElement.classList.add("dark");
}

createRoot(document.getElementById("root")).render(<App />);
