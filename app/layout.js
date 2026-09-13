import "./styles.css";
import BottomNav from "./BottomNav";

export const metadata = {
  title: "Sports Bet Tracker",
  description: "Personal sports bet tracker"
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <main className="shell">{children}</main>
        <BottomNav />
      </body>
    </html>
  );
}
