import { Dashboard } from "@/components/statline/dashboard";
export const metadata = { title: "Dashboard", robots: { index: false, follow: false } };
export default function Page() { return <main id="main-content" className="container"><div className="page-heading"><p className="eyebrow">YOUR STATLINE WORKSPACE</p><h1>Connected to your Gateway.</h1><p>Manage the access the existing service provides. Start with your API key or approved device.</p></div><Dashboard/></main>; }
