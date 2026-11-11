import { createContext } from "react";

export const AuthContext = createContext<(() => Promise<void>) | null>(null);
