import { useState } from "react";
import Header from "./components/Header";
import Overview from "./pages/Overview";
import Dag from "./pages/Dag";
import Knowledge from "./pages/Knowledge";
import Logs from "./pages/Logs";

type Tab = "overview" | "dag" | "knowledge" | "logs";

const pages: Record<Tab, { title: string; description: string }> = {
  overview: { title: "Overview", description: "Evaluation history and agent activity" },
  dag: { title: "Lineage", description: "Explore the evolution of each attempt" },
  knowledge: { title: "Knowledge", description: "Research notes, connections and shared skills" },
  logs: { title: "Logs", description: "Agent sessions and execution details" },
};

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  return (
    <div className="dashboard-shell">
      <Header activeTab={activeTab} onTabChange={setActiveTab} />

      <main className="workspace" id="main-content">
        <div className="page-heading">
          <h1>{pages[activeTab].title}</h1>
          <p>{pages[activeTab].description}</p>
        </div>
        <div className={`page-layout page-${activeTab}`}>
          {activeTab === "overview" && <Overview />}
          {activeTab === "dag" && <Dag />}
          {activeTab === "knowledge" && <Knowledge />}
          {activeTab === "logs" && <Logs />}
        </div>
      </main>
    </div>
  );
}
