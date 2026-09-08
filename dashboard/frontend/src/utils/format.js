export function formatTokens(num) {
  if (!num || isNaN(num) || num <= 0) return '0 tok';
  if (num >= 1_000_000) {
    return `${(num / 1_000_000).toFixed(2)}M tok`;
  }
  if (num >= 1_000) {
    return `${(num / 1_000).toFixed(1)}k tok`;
  }
  return `${num} tok`;
}

export function formatCost(cost) {
  const val = parseFloat(cost) || 0;
  if (val === 0) return '$0.0000';
  if (val < 0.0001) return '<$0.0001';
  return `$${val.toFixed(4)}`;
}

export function formatDuration(start, end) {
  if (!start) return '—';
  const startTime = new Date(start).getTime();
  const endTime = end ? new Date(end).getTime() : Date.now();
  const diffSec = Math.max(0, Math.floor((endTime - startTime) / 1000));

  if (diffSec < 60) return `${diffSec}s`;
  const mins = Math.floor(diffSec / 60);
  const secs = diffSec % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

export function formatTimestamp(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  } catch (e) {
    return isoStr.replace('T', ' ').slice(0, 19);
  }
}

export const STATE_STYLES = {
  DONE: {
    bg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    dot: 'bg-emerald-400',
    icon: '✓',
    text: 'text-emerald-400',
    label: 'DONE'
  },
  FAILED: {
    bg: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
    dot: 'bg-rose-400',
    icon: '✕',
    text: 'text-rose-400',
    label: 'FAILED'
  },
  RUNNING: {
    bg: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20 animate-pulse',
    dot: 'bg-cyan-400 animate-ping',
    icon: '▶',
    text: 'text-cyan-400',
    label: 'RUNNING'
  },
  INTERRUPTED: {
    bg: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    dot: 'bg-amber-400',
    icon: '⚡',
    text: 'text-amber-400',
    label: 'INTERRUPTED'
  },
  CANCELLED: {
    bg: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
    dot: 'bg-slate-400',
    icon: '◼',
    text: 'text-slate-400',
    label: 'CANCELLED'
  },
  PENDING: {
    bg: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
    dot: 'bg-indigo-400',
    icon: '◦',
    text: 'text-indigo-400',
    label: 'PENDING'
  }
};

export function getStateStyle(state) {
  return STATE_STYLES[state] || {
    bg: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
    dot: 'bg-slate-400',
    icon: '·',
    text: 'text-slate-400',
    label: state || 'UNKNOWN'
  };
}
