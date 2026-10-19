import { Dashboard } from "@/components/statline/dashboard";
export const metadata = { title: "Dashboard", robots: { index: false, follow: false } };
export default function Page() { return <main id="main-content" className="container"><div className="page-heading"><p className="eyebrow">YOUR STATLINE WORKSPACE</p><h1>Connected to your Gateway.</h1><p>View your Gateway identity and scopes without granting this website access to your device or installed apps.</p></div><Dashboard/></main>; }
