import { DashboardTools } from "@/components/statline/dashboard-tools";
import { Playground } from "@/components/statline/playground";
import { Dashboard } from "@/components/statline/dashboard";
export const metadata = { title: "Dashboard", robots: { index: false, follow: false } };
export default function Page() { return <main id="main-content" className="container"><div className="page-heading"><p className="eyebrow">YOUR STATLINE WORKSPACE</p><h1>Your Gateway workspace.</h1><p>Enroll this browser, request application API keys and scopes, track approvals, and manage existing credentials through the Gateway.</p></div><Dashboard/><DashboardTools/><section className="dashboard-content" style={{ marginTop: 44 }}><h2>Scoring workspace</h2><p>Run a live Gateway scoring request with your approved session or inspect source-verified samples.</p><Playground/></section></main>; }
