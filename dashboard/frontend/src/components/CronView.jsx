import React, { useState, useEffect } from 'react';
import { 
  Clock, 
  Terminal, 
  Bot, 
  Calendar, 
  FileCode, 
  Loader2, 
  Sparkles,
  Check,
  Copy
} from 'lucide-react';

export default function CronView() {
  const [activeTab, setActiveTab] = useState('all');
  const [data, setData] = useState({ system: [], emilia: [] });
  const [loading, setLoading] = useState(true);
  const [copiedIndex, setCopiedIndex] = useState(null);

  useEffect(() => {
    setLoading(true);
    fetch('/api/crons')
      .then((res) => res.json())
      .then((d) => {
        setData(d || { system: [], emilia: [] });
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to fetch crons:', err);
        setLoading(false);
      });
  }, []);

  const handleCopy = (text, key) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(key);
    setTimeout(() => setCopiedIndex(null), 1500);
  };

  const sysJobs = data.system || [];
  const emiliaJobs = data.emilia || [];

  return (
    <div className="flex flex-col gap-6 max-w-5xl mx-auto pb-16">
      
      {/* Header Banner */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 sm:p-6 backdrop-blur-md flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white">Cronjob Monitoring Hub</h1>
            <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              Live Schedules
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Overview of automated background workers, system crontabs, and autonomous agent tasks.
          </p>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1.5 bg-slate-950/80 p-1.5 rounded-xl border border-slate-800">
          <button
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activeTab === 'all'
                ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            All ({sysJobs.length + emiliaJobs.length})
          </button>
          <button
            onClick={() => setActiveTab('system')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activeTab === 'system'
                ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Terminal className="w-3 h-3" />
            <span>System ({sysJobs.length})</span>
          </button>
          <button
            onClick={() => setActiveTab('emilia')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activeTab === 'emilia'
                ? 'bg-emerald-500 text-slate-950 font-bold shadow-sm'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Bot className="w-3 h-3" />
            <span>Emilia ({emiliaJobs.length})</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-slate-900/40 rounded-2xl border border-slate-800">
          <Loader2 className="w-8 h-8 text-emerald-400 animate-spin mb-3" />
          <p className="text-sm font-semibold text-slate-300">Scanning scheduled crontabs...</p>
        </div>
      ) : (
        <div className="space-y-6">
          
          {/* Emilia Crons Section */}
          {(activeTab === 'all' || activeTab === 'emilia') && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-bold text-teal-300 px-1">
                <Bot className="w-4 h-4 text-teal-400" />
                <span>Autonomous Agent Crons (Emilia / Harness)</span>
                <span className="text-xs font-mono font-normal text-slate-500">
                  ({emiliaJobs.length} active)
                </span>
              </div>

              {emiliaJobs.length === 0 ? (
                <div className="p-6 bg-slate-900/40 border border-slate-800 rounded-xl text-center text-xs text-slate-500">
                  No durable Emilia crons scheduled in .claude/scheduled_tasks.json
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3">
                  {emiliaJobs.map((job, idx) => (
                    <div 
                      key={idx}
                      className="bg-slate-900/80 border border-teal-500/20 hover:border-teal-500/40 rounded-xl p-4 transition-all shadow-sm flex flex-col gap-2.5"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="px-2.5 py-1 rounded-md bg-teal-500/10 text-teal-300 border border-teal-500/30 text-xs font-mono font-bold">
                            {job.cron || '—'}
                          </span>
                          {job.human_schedule && (
                            <span className="text-xs text-slate-300 font-medium bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                              {job.human_schedule}
                            </span>
                          )}
                        </div>
                        <span className="text-[11px] font-mono text-slate-500 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                          {job.source?.split('/').slice(-2).join('/')}
                        </span>
                      </div>

                      <div className="text-sm font-medium text-slate-200 leading-relaxed font-sans bg-slate-950/40 p-3 rounded-lg border border-slate-800/60">
                        {job.prompt}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* System Crons Section */}
          {(activeTab === 'all' || activeTab === 'system') && (
            <div className="space-y-3 pt-2">
              <div className="flex items-center gap-2 text-sm font-bold text-cyan-300 px-1">
                <Terminal className="w-4 h-4 text-cyan-400" />
                <span>System Cronjobs (crontab & /etc/cron.d)</span>
                <span className="text-xs font-mono font-normal text-slate-500">
                  ({sysJobs.length} active)
                </span>
              </div>

              {sysJobs.length === 0 ? (
                <div className="p-6 bg-slate-900/40 border border-slate-800 rounded-xl text-center text-xs text-slate-500">
                  No system cronjobs discovered.
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3">
                  {sysJobs.map((job, idx) => (
                    <div 
                      key={idx}
                      className="bg-slate-900/80 border border-slate-800 hover:border-cyan-500/30 rounded-xl p-4 transition-all shadow-sm flex flex-col gap-2.5"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="px-2.5 py-1 rounded-md bg-cyan-500/10 text-cyan-300 border border-cyan-500/30 text-xs font-mono font-bold">
                            {job.schedule}
                          </span>
                          {job.human_schedule && (
                            <span className="text-xs text-slate-300 font-medium bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                              {job.human_schedule}
                            </span>
                          )}
                          {job.comment && (
                            <span className="text-xs text-slate-400 italic">
                              #{job.comment}
                            </span>
                          )}
                        </div>

                        <span className="text-[11px] font-mono text-slate-500 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                          {job.source}
                        </span>
                      </div>

                      <div className="relative group/cmd">
                        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded-lg border border-slate-800/80 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                          {job.command}
                        </pre>
                        <button
                          onClick={() => handleCopy(job.command, `sys-${idx}`)}
                          className="absolute right-2 top-2 p-1.5 rounded bg-slate-800/80 text-slate-400 hover:text-white opacity-0 group-hover/cmd:opacity-100 transition-opacity"
                          title="Copy command"
                        >
                          {copiedIndex === `sys-${idx}` ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

        </div>
      )}

    </div>
  );
}
