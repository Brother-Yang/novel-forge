import AppRouter from "./router";
import AuthGate from "./components/AuthGate";

export default function App() {
  return (
    <AuthGate>
      <AppRouter />
    </AuthGate>
  );
}
