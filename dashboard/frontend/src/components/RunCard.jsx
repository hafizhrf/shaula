import React, { useState } from 'react';
import { 
  Check, 
  Copy, 
  Clock, 
  Coins, 
  AlertCircle, 
  ChevronRight, 
  User, 
  ShieldAlert,
  Flame
} from 'lucide-react';
import { 
  formatTokens, 
  formatCost, 
  formatDuration, 
  formatTimestamp, 
  getStateStyle 
} from '../utils/format';

export default function RunCard({ run, onSelect }) {
  const [copiedId, setCopiedId] = useState(null);

  const style = getStateStyle(run.state);
  const cost = run.cost_usd || 0;
  const totalTokens = run.total_tokens || 0;
  const promptTokens = run.prompt_tokens || 0;
  const compTokens = run.completion_tokens || 0;
  const duration = formatDuration(run.created_at, run.finished_at);
  const createdAtFormatted = formatTimestamp(run.created_at);

  const handleCopy = (e, text, label) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopiedId(label);
    setTimeout(() => setCopiedId(null), 1500);
  };

  return (
    <div 
      onClick={() => onSelect(run.task_id)}
      className="group relative bg-slate-900/80 hover:bg-slate-800/80 border border-slate-800/90 hover:border-emerald-500/40 rounded-xl p-4 transition-all duration-200 shadow-sm hover:shadow-md hover:shadow-emerald-950/20 cursor-pointer flex flex-col gap-3"
    >
      {/* Top row: Status, Risk, Tokens & Cost badges */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {/* Status Badge */}
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${style.bg}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`}></span>
            <span>{style.label}</span>
          </span>

          {/* Risk Level Badge */}
          {run.risk_level && (
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${
              run.risk_level === 'DANGEROUS'
                ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                : 'bg-slate-800 text-slate-400 border border-slate-700'
            }`}>
              {run.risk_level === 'DANGEROUS' && <ShieldAlert className="w-3 h-3 text-rose-400" />}
              {run.risk_level}
            </span>
          )}

          {/* Account */}
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400 bg-slate-800/50 px-2 py-0.5 rounded border border-slate-700/50">
            <User className="w-3 h-3 text-slate-500" />
            {run.account || 'default'}
          </span>
        </div>

        {/* Tokens & Cost Badges */}
        <div className="flex items-center gap-1.5">
          {/* Token usage badge */}
          <span 
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-300 border border-amber-500/20 text-xs font-mono font-medium"
            title={`Prompt: ${promptTokens.toLocaleString()} | Completion: ${compTokens.toLocaleString()}`}
          >
            <Coins className="w-3 h-3 text-amber-400" />
            <span>{formatTokens(totalTokens)}</span>
          </span>

          {/* Cost badge */}
          <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 text-xs font-mono font-medium">
            {formatCost(cost)}
          </span>
        </div>
      </div>

      {/* Description / Prompt preview */}
      <div className="text-sm font-medium text-slate-200 line-clamp-2 leading-relaxed">
        {run.description || <span className="text-slate-500 italic">No description provided</span>}
      </div>

      {/* Error banner if present */}
      {run.error_text && (
        <div className="flex items-start gap-2 bg-rose-950/30 border border-rose-800/40 rounded-lg p-2.5 text-xs text-rose-300">
          <AlertCircle className="w-3.5 h-3.5 text-rose-400 mt-0.5 shrink-0" />
          <span className="line-clamp-2 font-mono">{run.error_text}</span>
        </div>
      )}

      {/* Bottom Footer: Task ID, Session ID, Duration, Time & View link */}
      <div className="flex flex-wrap items-center justify-between text-xs text-slate-400 border-t border-slate-800/60 pt-2.5 mt-1 gap-2">
        <div className="flex items-center gap-2">
          {/* Task ID chip */}
          <button
            onClick={(e) => handleCopy(e, run.task_id, 'task')}
            title="Click to copy full Task ID"
            className="group/id inline-flex items-center gap-1 font-mono text-[11px] text-slate-400 hover:text-slate-200 bg-slate-800/40 hover:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-800 hover:border-slate-700 transition-colors"
          >
            <span>{(run.task_id || '').slice(0, 8)}</span>
            {copiedId === 'task' ? (
              <Check className="w-2.5 h-2.5 text-emerald-400" />
            ) : (
              <Copy className="w-2.5 h-2.5 opacity-50 group-hover/id:opacity-100" />
            )}
          </button>

          {/* Session ID chip */}
          {run.session_id && (
            <button
              onClick={(e) => handleCopy(e, run.session_id, 'session')}
              title="Click to copy full Session ID"
              className="group/id inline-flex items-center gap-1 font-mono text-[11px] text-slate-500 hover:text-slate-300 bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800 hover:border-slate-700 transition-colors"
            >
              <span>{(run.session_id || '').slice(0, 8)}</span>
              {copiedId === 'session' ? (
                <Check className="w-2.5 h-2.5 text-emerald-400" />
              ) : (
                <Copy className="w-2.5 h-2.5 opacity-40 group-hover/id:opacity-100" />
              )}
            </button>
          )}

          {run.turn > 1 && (
            <span className="text-[11px] text-slate-500 font-mono">
              turn #{run.turn}
            </span>
          )}
        </div>

        {/* Timestamps & Action */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-slate-400">
            <Clock className="w-3 h-3 text-slate-500" />
            <span>{createdAtFormatted}</span>
            <span className="text-slate-600">·</span>
            <span className="font-mono text-slate-300 font-medium">{duration}</span>
          </div>

          <span className="flex items-center gap-0.5 text-emerald-400 font-semibold text-xs group-hover:translate-x-0.5 transition-transform">
            <span>Details</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </span>
        </div>
      </div>

    </div>
  );
}
