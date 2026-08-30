import "./globals.css";
import { defaultLocale } from "../lib/locales";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang={defaultLocale}>
      <body>
        <div className="hazard-stripe h-2 w-full shrink-0" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
