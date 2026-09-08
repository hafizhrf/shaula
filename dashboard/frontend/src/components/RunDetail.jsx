import React, { useEffect, useState } from 'react';
import { 
  ArrowLeft, 
  Copy, 
  Check, 
  Clock, 
  Coins, 
  DollarSign, 
  Terminal, 
  Brain, 
  User, 
  Bot, 
  ChevronDown, 
  ChevronRight, 
  AlertTriangle, 
  Info, 
  FileText,
  ShieldAlert,
  Loader2,
  ExternalLink
} from 'lucide-react';
import { marked } from 'marked';
import { 
  formatTokens, 
  formatCost, 
  formatDuration, 
  formatTimestamp, 
  getStateStyle 
} from '../utils/format';

export default function RunDetail({ taskId, onBack }) {
  const [run, setRun] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [loadingRun, setLoadingRun] = useState(true);
  const [loadingTranscript, setLoadingTranscript] = useState(true);
  const [error, setError] = useState(null);
  const [copiedKey, setCopiedKey] = useState(null);
  const [openDrawers, setOpenDrawers] = useState({});

  useEffect(() => {
    if (!taskId) return;
    window.scrollTo(0, 0);

    // Fetch Run metadata
    setLoadingRun(true);
    fetch(`/api/runs/${taskId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`Run ${taskId} not found`);
        return res.json();
      })
      .then((data) => {
        setRun(data);
        setLoadingRun(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoadingRun(false);
      });

    // Fetch Transcript
    setLoadingTranscript(true);
    fetch(`/api/runs/${taskId}/transcript`)
      .then((res) => res.json())
      .then((data) => {
        setTranscript(data);
        setLoadingTranscript(false);
      })
      .catch((err) => {
        console.error('Failed to load transcript:', err);
        setTranscript({ found: false, steps: [] });
        setLoadingTranscript(false);
      });
  }, [taskId]);

  const toggleDrawer = (idx) => {
    setOpenDrawers((prev) => ({
      ...prev,
      [idx]: !prev[idx]
    }));
  };

  const handleCopy = (text, key) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 1500);
  };

  if (loadingRun) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <Loader2 className="w-8 h-8 text-emerald-400 animate-spin mb-3" />
        <p className="text-sm font-semibold text-slate-300">Loading run recap...</p>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="max-w-4xl mx-auto py-12">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-2 text-xs font-semibold text-slate-400 hover:text-white mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Runs</span>
        </button>
        <div className="bg-rose-950/20 border border-rose-800/40 rounded-xl p-6 text-center">
          <AlertTriangle className="w-8 h-8 text-rose-400 mx-auto mb-2" />
          <h2 className="text-lg font-bold text-white">Execution Not Found</h2>
          <p className="text-xs text-slate-400 mt-1">{error || 'Could not load details for this task.'}</p>
        </div>
      </div>
    );
  }

  const style = getStateStyle(run.state);
  const cost = run.cost_usd || 0;
  const totalTokens = run.total_tokens || 0;
  const promptTokens = run.prompt_tokens || 0;
  const compTokens = run.completion_tokens || 0;
  const duration = formatDuration(run.created_at, run.finished_at);

  return (
    <div className="flex flex-col gap-6 max-w-5xl mx-auto pb-16">
      
      {/* Back Button */}
      <div>
        <button
          onClick={onBack}
          className="inline-flex items-center gap-2 text-xs font-bold text-slate-400 hover:text-emerald-400 transition-colors bg-slate-900/60 border border-slate-800 hover:border-emerald-500/30 px-3 py-1.5 rounded-lg"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Runs</span>
        </button>
      </div>

      {/* Header Card: Overview & Metadata */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 sm:p-6 shadow-xl relative overflow-hidden backdrop-blur-md">
        
        {/* Glow accent */}
        <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-emerald-500 via-teal-400 to-cyan-500"></div>

        <div className="flex flex-col gap-5">
          
          {/* Top row: Status, Risk, Account, and Timestamps */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2.5">
              <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border ${style.bg}`}>
                <span className={`w-2 h-2 rounded-full ${style.dot}`}></span>
                <span>{style.label}</span>
              </span>

              {run.risk_level && (
                <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wider ${
                  run.risk_level === 'DANGEROUS'
                    ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                    : 'bg-slate-800 text-slate-300 border border-slate-700'
                }`}>
                  {run.risk_level === 'DANGEROUS' && <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />}
                  <span>{run.risk_level}</span>
                </span>
              )}

              <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-300 bg-slate-800/80 px-2.5 py-1 rounded-md border border-slate-700">
                <User className="w-3.5 h-3.5 text-slate-400" />
                <span>Account: <strong className="text-white">{run.account || 'default'}</strong></span>
              </span>

              {run.turn && (
                <span className="text-xs text-slate-400 font-mono bg-slate-800/50 px-2 py-1 rounded border border-slate-800">
                  Turn #{run.turn}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2 text-xs text-slate-400 font-mono">
              <Clock className="w-3.5 h-3.5 text-slate-500" />
              <span>{formatTimestamp(run.created_at)}</span>
              <span className="text-slate-600">→</span>
              <span>{run.finished_at ? formatTimestamp(run.finished_at) : 'In progress'}</span>
              <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-bold ml-1">
                {duration}
              </span>
            </div>
          </div>

          {/* Description */}
          <div>
            <h1 className="text-base sm:text-lg font-bold text-white leading-relaxed">
              {run.description || 'No description recorded'}
            </h1>
          </div>

          {/* Key Metrics Grid: Tokens & Cost */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
            
            <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1">
                <Coins className="w-3 h-3 text-amber-400" /> Total Tokens
              </span>
              <span className="text-lg font-extrabold font-mono text-amber-300 mt-1">
                {formatTokens(totalTokens)}
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                {totalTokens.toLocaleString()} tok
              </span>
            </div>

            <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1">
                <Coins className="w-3 h-3 text-blue-400" /> Prompt Tokens
              </span>
              <span className="text-lg font-extrabold font-mono text-blue-300 mt-1">
                {formatTokens(promptTokens)}
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                {promptTokens.toLocaleString()} tok
              </span>
            </div>

            <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1">
                <Coins className="w-3 h-3 text-emerald-400" /> Completion Tokens
              </span>
              <span className="text-lg font-extrabold font-mono text-emerald-300 mt-1">
                {formatTokens(compTokens)}
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                {compTokens.toLocaleString()} tok
              </span>
            </div>

            <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col">
              <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1">
                <DollarSign className="w-3 h-3 text-emerald-400" /> USD Cost
              </span>
              <span className="text-lg font-extrabold font-mono text-emerald-300 mt-1">
                {formatCost(cost)}
              </span>
              <span className="text-[10px] text-slate-500 font-mono">
                estimated spend
              </span>
            </div>

          </div>

          {/* Copyable IDs Row */}
          <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-800/60 text-xs">
            <div className="flex items-center gap-1.5 bg-slate-950/70 px-2.5 py-1 rounded-lg border border-slate-800">
              <span className="text-slate-500 font-semibold">Task ID:</span>
              <span className="font-mono text-slate-300 font-medium">{run.task_id}</span>
              <button
                onClick={() => handleCopy(run.task_id, 'task')}
                className="text-slate-400 hover:text-emerald-400 p-0.5 ml-1 transition-colors"
                title="Copy Task ID"
              >
                {copiedKey === 'task' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
              </button>
            </div>

            {run.session_id && (
              <div className="flex items-center gap-1.5 bg-slate-950/70 px-2.5 py-1 rounded-lg border border-slate-800">
                <span className="text-slate-500 font-semibold">Session ID:</span>
                <span className="font-mono text-slate-300 font-medium">{run.session_id}</span>
                <button
                  onClick={() => handleCopy(run.session_id, 'session')}
                  className="text-slate-400 hover:text-emerald-400 p-0.5 ml-1 transition-colors"
                  title="Copy Session ID"
                >
                  {copiedKey === 'session' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                </button>
              </div>
            )}

            {run.channel_id && (
              <div className="flex items-center gap-1.5 bg-slate-950/70 px-2.5 py-1 rounded-lg border border-slate-800 text-slate-400">
                <span className="text-slate-500 font-semibold">Channel:</span>
                <span className="font-mono text-slate-300">{run.channel_id}</span>
              </div>
            )}
          </div>

          {/* Error Banner */}
          {run.error_text && (
            <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl p-4 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
              <div className="flex flex-col gap-1">
                <span className="font-bold text-xs text-rose-300 uppercase tracking-wide">Execution Error</span>
                <pre className="font-mono text-xs text-rose-200 whitespace-pre-wrap break-all leading-relaxed">
                  {run.error_text}
                </pre>
              </div>
            </div>
          )}

        </div>
      </div>

      {/* Conversation Recap & Step Timeline */}
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <FileText className="w-4 h-4 text-emerald-400" />
            <span>Conversation Timeline & Execution Trace</span>
          </h2>
          {transcript?.steps && (
            <span className="text-xs text-slate-400 font-mono">
              {transcript.steps.length} steps recorded
            </span>
          )}
        </div>

        {loadingTranscript ? (
          <div className="flex flex-col items-center justify-center py-16 bg-slate-900/40 rounded-2xl border border-slate-800">
            <Loader2 className="w-6 h-6 text-emerald-400 animate-spin mb-2" />
            <span className="text-xs text-slate-400">Parsing session transcript logs...</span>
          </div>
        ) : !transcript?.found || !transcript.steps || transcript.steps.length === 0 ? (
          /* Graceful Fallback Banner */
          <div className="bg-slate-900/50 border border-slate-800 rounded-2xl p-6 flex flex-col items-center text-center gap-3">
            <div className="w-12 h-12 rounded-full bg-slate-800/80 flex items-center justify-center text-slate-400">
              <Info className="w-6 h-6 text-slate-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-200">Transcript Not Available on Disk</h3>
              <p className="text-xs text-slate-400 mt-1 max-w-md">
                Detailed step-by-step logs for this session have either expired, were pruned by workspace cleanup, or were executed in a transient worker. The high-level execution parameters and token metrics above remain permanently preserved.
              </p>
            </div>
          </div>
        ) : (
          /* Step-by-Step Interactive Timeline */
          <div className="space-y-4">
            {transcript.steps.map((step, idx) => {
              
              // 1. User Prompt
              if (step.type === 'user') {
                return (
                  <div key={idx} className="flex items-start gap-3 bg-slate-900/90 border border-slate-800 rounded-2xl p-4 shadow-sm">
                    <div className="w-8 h-8 rounded-xl bg-blue-500/10 border border-blue-500/30 flex items-center justify-center text-blue-400 shrink-0">
                      <User className="w-4 h-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-bold text-blue-300">User Prompt</span>
                        {step.timestamp && (
                          <span className="text-[11px] text-slate-500 font-mono">{formatTimestamp(step.timestamp)}</span>
                        )}
                      </div>
                      <div className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed font-sans break-words bg-slate-950/50 p-3 rounded-xl border border-slate-800/60">
                        {step.text}
                      </div>
                    </div>
                  </div>
                );
              }

              // 2. Model Reasoning / Thinking Accordion
              if (step.type === 'thinking') {
                const isOpen = openDrawers[idx];
                return (
                  <div key={idx} className="border border-purple-500/20 rounded-2xl bg-purple-950/10 overflow-hidden">
                    <button
                      onClick={() => toggleDrawer(idx)}
                      className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-purple-900/20 transition-colors"
                    >
                      <div className="flex items-center gap-2.5">
                        <div className="w-7 h-7 rounded-lg bg-purple-500/20 flex items-center justify-center text-purple-300">
                          <Brain className="w-4 h-4" />
                        </div>
                        <div>
                          <span className="text-xs font-bold text-purple-300">Model Thinking / Chain of Thought</span>
                          <span className="text-[11px] text-purple-400/60 ml-2 font-mono">({step.text.length} chars)</span>
                        </div>
                      </div>
                      {isOpen ? (
                        <ChevronDown className="w-4 h-4 text-purple-400" />
                      ) : (
                        <ChevronRight className="w-4 h-4 text-purple-400" />
                      )}
                    </button>

                    {isOpen && (
                      <div className="p-4 pt-1 border-t border-purple-500/10 bg-slate-950/60">
                        <pre className="text-xs text-purple-200/90 whitespace-pre-wrap font-mono leading-relaxed max-h-96 overflow-y-auto pr-2">
                          {step.text}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              }

              // 3. Tool Execution Call
              if (step.type === 'tool_call') {
                const isOpen = openDrawers[idx] ?? false;
                return (
                  <div key={idx} className="border border-slate-800 rounded-2xl bg-slate-900/60 overflow-hidden shadow-sm">
                    <div 
                      onClick={() => toggleDrawer(idx)}
                      className="px-4 py-3 flex items-center justify-between cursor-pointer hover:bg-slate-800/50 transition-colors"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className="w-7 h-7 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 shrink-0">
                          <Terminal className="w-3.5 h-3.5" />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold font-mono text-cyan-300">
                              {step.name}
                            </span>
                            {step.args?.CommandLine && (
                              <span className="text-xs text-slate-400 font-mono truncate max-w-xs sm:max-w-md">
                                : {step.args.CommandLine}
                              </span>
                            )}
                            {step.args?.AbsolutePath && (
                              <span className="text-xs text-slate-400 font-mono truncate max-w-xs">
                                : {step.args.AbsolutePath.split('/').pop()}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[11px] text-slate-500 font-medium">
                          {isOpen ? 'Collapse' : 'Expand'}
                        </span>
                        {isOpen ? (
                          <ChevronDown className="w-4 h-4 text-slate-400" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-slate-400" />
                        )}
                      </div>
                    </div>

                    {isOpen && (
                      <div className="p-4 border-t border-slate-800 bg-slate-950/80 space-y-3">
                        {/* Parameters */}
                        {step.args && Object.keys(step.args).length > 0 && (
                          <div>
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                              Parameters
                            </span>
                            <pre className="text-xs font-mono text-cyan-200 bg-slate-900 p-2.5 rounded-lg border border-slate-800 overflow-x-auto">
                              {JSON.stringify(step.args, null, 2)}
                            </pre>
                          </div>
                        )}

                        {/* Output */}
                        {step.output && (
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                                Tool Output / Stdout
                              </span>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCopy(step.output, `tool-${idx}`);
                                }}
                                className="text-[11px] text-slate-400 hover:text-emerald-400 flex items-center gap-1"
                              >
                                {copiedKey === `tool-${idx}` ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                                <span>Copy</span>
                              </button>
                            </div>
                            <pre className="text-xs font-mono text-slate-300 bg-slate-900/90 p-3 rounded-lg border border-slate-800 overflow-x-auto max-h-72 overflow-y-auto leading-relaxed whitespace-pre-wrap">
                              {step.output}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              }

              // 4. Assistant Response
              if (step.type === 'assistant') {
                const rawHtml = marked.parse(step.text || '');
                return (
                  <div key={idx} className="flex items-start gap-3 bg-slate-900/90 border border-slate-800 rounded-2xl p-4 sm:p-5 shadow-sm">
                    <div className="w-8 h-8 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0">
                      <Bot className="w-4 h-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-emerald-400">Assistant Response</span>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleCopy(step.text, `asst-${idx}`)}
                            className="text-[11px] text-slate-400 hover:text-emerald-400 flex items-center gap-1"
                          >
                            {copiedKey === `asst-${idx}` ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                            <span>Copy Markdown</span>
                          </button>
                          {step.timestamp && (
                            <span className="text-[11px] text-slate-500 font-mono">{formatTimestamp(step.timestamp)}</span>
                          )}
                        </div>
                      </div>
                      <div 
                        className="markdown-body text-sm text-slate-200 leading-relaxed font-sans overflow-hidden"
                        dangerouslySetInnerHTML={{ __html: rawHtml }}
                      />
                    </div>
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}

      </div>

    </div>
  );
}
