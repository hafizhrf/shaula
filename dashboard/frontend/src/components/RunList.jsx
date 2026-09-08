import React from 'react';
import { 
  Search, 
  Filter, 
  ChevronLeft, 
  ChevronRight, 
  RotateCcw, 
  Inbox, 
  Loader2 
} from 'lucide-react';
import RunCard from './RunCard';

const STATES = ['ALL', 'DONE', 'RUNNING', 'FAILED', 'INTERRUPTED', 'CANCELLED', 'PENDING'];

export default function RunList({
  runs,
  total,
  limit,
  offset,
  stateFilter,
  searchQuery,
  isLoading,
  onStateChange,
  onSearchChange,
  onPageChange,
  onSelectRun
}) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit) || 1;
  const startIdx = total > 0 ? offset + 1 : 0;
  const endIdx = Math.min(offset + limit, total);

  return (
    <div className="flex flex-col gap-5">
      {/* Controls Header: Search & State Filter */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-3 sm:p-4 backdrop-blur-sm flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
        
        {/* Search Input */}
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search by prompt description, task ID, session ID, or account..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full bg-slate-950/70 border border-slate-700/80 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all font-sans"
          />
          {searchQuery && (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-200 bg-slate-800 px-1.5 py-0.5 rounded"
            >
              Clear
            </button>
          )}
        </div>

        {/* State Filter Buttons */}
        <div className="flex items-center gap-1 overflow-x-auto pb-1 md:pb-0 scrollbar-none">
          {STATES.map((st) => {
            const isSelected = (stateFilter === '' && st === 'ALL') || stateFilter === st;
            return (
              <button
                key={st}
                onClick={() => onStateChange(st === 'ALL' ? '' : st)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                  isSelected
                    ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm shadow-emerald-500/20'
                    : 'bg-slate-950/60 text-slate-400 hover:text-slate-200 hover:bg-slate-800/80 border border-slate-800'
                }`}
              >
                {st}
              </button>
            );
          })}
        </div>

      </div>

      {/* Pagination & Count Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between text-xs text-slate-400 px-1 gap-2">
        <div className="font-medium">
          Showing <span className="font-bold text-slate-200 font-mono">{startIdx}</span> to{' '}
          <span className="font-bold text-slate-200 font-mono">{endIdx}</span> of{' '}
          <span className="font-bold text-slate-200 font-mono">{total}</span> runs
        </div>

        <div className="flex items-center gap-2">
          <span className="text-slate-400">
            Page <span className="font-semibold text-slate-200 font-mono">{currentPage}</span> of{' '}
            <span className="font-semibold text-slate-200 font-mono">{totalPages}</span>
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              disabled={offset <= 0 || isLoading}
              className="p-1.5 rounded-md bg-slate-800/80 hover:bg-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed border border-slate-700 transition-colors"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => onPageChange(offset + limit)}
              disabled={offset + limit >= total || isLoading}
              className="p-1.5 rounded-md bg-slate-800/80 hover:bg-slate-700 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed border border-slate-700 transition-colors"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Runs Card Stream */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-slate-900/30 rounded-2xl border border-slate-800/50">
          <Loader2 className="w-8 h-8 text-emerald-400 animate-spin mb-3" />
          <p className="text-sm font-semibold text-slate-300">Loading runs history...</p>
          <p className="text-xs text-slate-500 mt-1">Fetching latest executions</p>
        </div>
      ) : runs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 bg-slate-900/30 rounded-2xl border border-dashed border-slate-800">
          <div className="w-12 h-12 rounded-full bg-slate-800/80 flex items-center justify-center text-slate-400 mb-3">
            <Inbox className="w-6 h-6" />
          </div>
          <h3 className="text-base font-bold text-slate-200">No runs found</h3>
          <p className="text-xs text-slate-400 mt-1 max-w-sm text-center">
            {searchQuery || stateFilter 
              ? 'No execution records matched your filter criteria. Try clearing search filters.' 
              : 'There are no recorded task runs in the database yet.'}
          </p>
          {(searchQuery || stateFilter) && (
            <button
              onClick={() => {
                onSearchChange('');
                onStateChange('');
              }}
              className="mt-4 px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20 transition-colors"
            >
              Reset Filters
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {runs.map((run) => (
            <RunCard
              key={run.task_id}
              run={run}
              onSelect={onSelectRun}
            />
          ))}
        </div>
      )}

      {/* Bottom Pagination */}
      {total > limit && (
        <div className="flex justify-center items-center gap-2 pt-4 pb-8">
          <button
            onClick={() => onPageChange(Math.max(0, offset - limit))}
            disabled={offset <= 0 || isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed border border-slate-700 transition-colors"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            <span>Previous</span>
          </button>
          <span className="text-xs text-slate-400 px-2 font-mono">
            {currentPage} / {totalPages}
          </span>
          <button
            onClick={() => onPageChange(offset + limit)}
            disabled={offset + limit >= total || isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed border border-slate-700 transition-colors"
          >
            <span>Next</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
