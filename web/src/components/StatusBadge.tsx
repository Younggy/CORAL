export default function StatusBadge({ status }: { status: string }) {
  return <span className="status-badge" data-status={status}>{status}</span>;
}
