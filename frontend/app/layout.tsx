import type { Metadata } from "next";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Synapse / Your academic second brain",
  description: "The FSM Club academic workspace. Connect your campus, courses and curiosity.",
  robots: { index: false, follow: false }
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body><a href="#main" className="skip-link">Skip to workspace</a>{children}</body></html>;
}
