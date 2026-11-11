import { Routes, Route } from "react-router-dom";
import AsyncContent from "../components/AsyncContent";
import { routePatterns } from "./paths";

const loadBooks = () => import("../pages/BookList");
const loadWorkspace = () => import("../pages/BookWorkspace");
const loadAgent = () => import("../pages/AgentChat");

export default function AppRouter() {
  return (
    <Routes>
      <Route
        path={routePatterns.home}
        element={<AsyncContent key="books" load={loadBooks} componentProps={{}} label="书架" />}
      />
      <Route
        path={routePatterns.book}
        element={
          <AsyncContent key="workspace" load={loadWorkspace} componentProps={{}} label="工作区" />
        }
      />
      <Route
        path={routePatterns.agentChat}
        element={<AsyncContent key="agent" load={loadAgent} componentProps={{}} label="AI 创作" />}
      />
    </Routes>
  );
}
