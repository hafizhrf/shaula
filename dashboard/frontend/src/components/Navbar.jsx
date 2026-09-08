import React from 'react';
import { 
  Layers, 
  Clock, 
  Coins, 
  DollarSign, 
  CheckCircle2, 
  XCircle, 
  PlayCircle, 
  RefreshCw, 
  LogOut,
  Sparkles
} from 'lucide-react';
import { formatTokens, formatCost } from '../utils/format';

export default function Navbar({ 
  currentPath, 
  onNavigate, 
  stats, 
  onRefresh, 
  isRefreshing, 
  onLogout 
}) {
  const nDone = stats?.DONE || 0;
  const nFailed = stats?.FAILED || 0;
  const nRunning = stats?.RUNNING || 0;
  const nInterrupted = stats?.INTERRUPTED || 0;
  const totalRuns = stats?.total_runs || 0;
  const totalTokens = stats?.total_tokens_sum || 0;
  const totalCost = stats?.total_cost_sum || 0;

  return (
    <header className="sticky top-0 z-40 bg-slate-900/90 backdrop-blur-md border-b border-slate-800/80 shadow-lg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between py-3 gap-3">
          
          {/* Logo & Navigation Tabs */}
          <div className="flex items-center justify-between gap-6">
            <div 
              onClick={() => onNavigate('/')}
              className="flex items-center gap-2.5 cursor-pointer group"
            >
              <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 p-0.5 flex items-center justify-center shadow-md shadow-emerald-500/20 group-hover:scale-105 transition-transform">
                <div className="w-full h-full bg-slate-950 rounded-[10px] flex items-center justify-center">
                  <Sparkles className="w-5 h-5 text-emerald-400 group-hover:rotate-12 transition-transform" />
                </div>
              </div>
              <div>
                <div className="flex items-center gap-1.5">
                  <span className="font-extrabold text-base tracking-tight text-white group-hover:text-emerald-300 transition-colors">
                    Runs History
                  </span>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    Shaula Edition
                  </span>
                </div>
                <p className="text-xs text-slate-400 font-medium">DevOps & Agent Observability</p>
              </div>
            </div>

            {/* Main Tabs */}
            <nav className="flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
              <button
                onClick={() => onNavigate('/')}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                  currentPath === '/' || currentPath.startsWith('/run')
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <Layers className="w-3.5 h-3.5" />
                <span>Runs</span>
              </button>
              <button
                onClick={() => onNavigate('/cron')}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                  currentPath === '/cron'
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <Clock className="w-3.5 h-3.5" />
                <span>Cron Jobs</span>
              </button>
            </nav>
          </div>

          {/* Aggregate Metrics Bar & Actions */}
          <div className="flex flex-wrap items-center gap-2 justify-between md:justify-end">
            <div className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
              
              {/* Total runs */}
              <div className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-800/60 border border-slate-700/60 text-slate-300" title="Total Executed Runs">
                <span className="text-slate-400 font-semibold">Total:</span>
                <span className="font-bold text-white font-mono">{totalRuns}</span>
              </div>

              {/* Status pills */}
              {nRunning > 0 && (
                <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 animate-pulse" title="Active Running Tasks">
                  <PlayCircle className="w-3.5 h-3.5" />
                  <span className="font-bold font-mono">{nRunning}</span>
                </div>
              )}

              <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400" title="Successful Tasks">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span className="font-bold font-mono">{nDone}</span>
              </div>

              {nFailed > 0 && (
                <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400" title="Failed Tasks">
                  <XCircle className="w-3.5 h-3.5" />
                  <span className="font-bold font-mono">{nFailed}</span>
                </div>
              )}

              {/* Total Tokens Pill */}
              <div className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 shadow-sm" title="Total Tokens Processed">
                <Coins className="w-3.5 h-3.5 text-amber-400" />
                <span className="font-bold font-mono">{formatTokens(totalTokens)}</span>
              </div>

              {/* Total USD Cost Pill */}
              <div className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 shadow-sm" title="Total Estimated USD Cost">
                <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-bold font-mono">{formatCost(totalCost)}</span>
              </div>
            </div>

            {/* Quick Actions: Refresh & Logout */}
            <div className="flex items-center gap-1.5 ml-1">
              <button
                onClick={onRefresh}
                disabled={isRefreshing}
                title="Refresh runs"
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 border border-slate-700/60 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-emerald-400' : ''}`} />
              </button>

              <button
                onClick={onLogout}
                title="Sign out"
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-rose-300 hover:bg-rose-500/10 border border-slate-700/60 hover:border-rose-500/30 transition-all"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Logout</span>
              </button>
            </div>

          </div>

        </div>
      </div>
    </header>
  );
}
