import { Routes, Route } from "react-router-dom";
import BookList from "../pages/BookList";
import BookWorkspace from "../pages/BookWorkspace";
import { routePatterns } from "./paths";

export default function AppRouter() {
  return (
    <Routes>
      <Route path={routePatterns.home} element={<BookList />} />
      <Route path={routePatterns.book} element={<BookWorkspace />} />
    </Routes>
  );
}
