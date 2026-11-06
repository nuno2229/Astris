const p = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
export const HomeIcon = () => <svg viewBox="0 0 24 24" {...p}><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /></svg>;
export const ListIcon = () => <svg viewBox="0 0 24 24" {...p}><path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.5" cy="6" r="1" /><circle cx="3.5" cy="12" r="1" /><circle cx="3.5" cy="18" r="1" /></svg>;
export const WalletIcon = () => <svg viewBox="0 0 24 24" {...p}><rect x="3" y="6" width="18" height="14" rx="3" /><path d="M16 13h2M3 10h18" /></svg>;
export const UsersIcon = () => <svg viewBox="0 0 24 24" {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5" /><path d="M16 4.5a3.5 3.5 0 010 7M18 14.8c1.8.8 3 2.6 3.5 5.2" /></svg>;
export const GearIcon = () => <svg viewBox="0 0 24 24" width="22" height="22" {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></svg>;
export const StarMark = ({ className = "mark" }: { className?: string }) => (
  <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
    <path fill="currentColor" d="M16 2.5l2.6 9.4 9.4 4.1-9.4 4.1L16 29.5l-2.6-9.4L4 16l9.4-4.1z" />
    <circle cx="26" cy="6" r="1.6" fill="currentColor" opacity=".7" />
  </svg>
);
export const ChevronDown = () => <svg viewBox="0 0 24 24" {...p}><path d="M6 9l6 6 6-6" /></svg>;
export const BillsIcon = () => <svg viewBox="0 0 24 24" {...p}><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" /><path d="M9 8h6M9 12h6M9 16h3" /></svg>;
export const PantryIcon = () => <svg viewBox="0 0 24 24" {...p}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M4 9h16M4 15h16M9 6h1M9 12h1M9 18h1" /></svg>;
export const MoreIcon = () => <svg viewBox="0 0 24 24" {...p}><circle cx="5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="19" cy="12" r="1.5" /></svg>;
