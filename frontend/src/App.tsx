import { Routes, Route } from "react-router-dom";
import BookList from "./pages/BookList";
import BookWorkspace from "./pages/BookWorkspace";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<BookList />} />
      <Route path="/books/:bookId" element={<BookWorkspace />} />
    </Routes>
  );
}
