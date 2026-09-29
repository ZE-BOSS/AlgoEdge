import { NavLink, Routes, Route } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Users, Gauge, Inbox, Eye, LineChart, Scale, FileText, History, Percent, UserPlus,
} from 'lucide-react';
import { inv } from '../../services/api';
import Overview from './Overview';
import InvestorList from './InvestorList';
import InvestorDetail from './InvestorDetail';
import Queues from './Queues';
import Disclosure from './Disclosure';
import Nav from './Nav';
import Reconciliation from './Reconciliation';
import Terms from './Terms';
import Audit from './Audit';
import Fees from './Fees';
import Applications from './Applications';

function SectionNav() {
  const { data } = useQuery({ queryKey: ['inv', 'overview'], queryFn: inv.overview });
  const q = data?.data?.queues || {};
  const queueCount = (q.deposits || 0) + (q.withdrawals || 0) + (q.exceptions || 0)
    + (q.to_pay || 0) + (q.closures || 0);

  const tabs = [
    { to: '/investors', label: 'Overview', icon: Gauge, end: true },
    { to: '/investors/list', label: 'Investors', icon: Users },
    { to: '/investors/applications', label: 'Applications', icon: UserPlus, badge: q.applications },
    { to: '/investors/queues', label: 'Queues', icon: Inbox, badge: queueCount },
    { to: '/investors/disclosure', label: 'Disclosure', icon: Eye, badge: q.disclosures },
    { to: '/investors/nav', label: 'NAV', icon: LineChart },
    { to: '/investors/fees', label: 'Fees', icon: Percent },
    { to: '/investors/reconciliation', label: 'Reconciliation', icon: Scale },
    { to: '/investors/terms', label: 'Fund terms', icon: FileText },
    { to: '/investors/audit', label: 'Audit', icon: History },
  ];

  return (
    <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
      {tabs.map(({ to, label, icon: Icon, end, badge }) => (
        <NavLink key={to} to={to} end={end}
                 className={({ isActive }) => `btn ${isActive ? 'btn-primary' : 'btn-secondary'} btn-sm`}>
          <Icon size={14} /> {label}
          {badge > 0 && <span className="inv-count">{badge}</span>}
        </NavLink>
      ))}
    </div>
  );
}

export default function InvestorsPage() {
  return (
    <div className="inv-section">
      <div className="page-header">
        <h2><Users size={22} style={{ display: 'inline', marginRight: 8 }} />Investors</h2>
        <p>The pool's register, money in and out, what investors are shown, and whether it all adds up</p>
      </div>

      <SectionNav />

      <Routes>
        <Route index element={<Overview />} />
        <Route path="list" element={<InvestorList />} />
        <Route path="applications" element={<Applications />} />
        <Route path="p/:id" element={<InvestorDetail />} />
        <Route path="queues/*" element={<Queues />} />
        <Route path="disclosure" element={<Disclosure />} />
        <Route path="nav" element={<Nav />} />
        <Route path="fees" element={<Fees />} />
        <Route path="reconciliation" element={<Reconciliation />} />
        <Route path="terms" element={<Terms />} />
        <Route path="audit" element={<Audit />} />
      </Routes>
    </div>
  );
}
