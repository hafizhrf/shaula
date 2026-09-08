import React, { useState, useEffect, useCallback } from 'react';
import Navbar from './components/Navbar';
import RunList from './components/RunList';
import RunDetail from './components/RunDetail';
import CronView from './components/CronView';
import LoginModal from './components/LoginModal';
import { Loader2 } from 'lucide-react';

export default function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname);
  const [isAuthenticated, setIsAuthenticated] = useState(null); // null = checking, true, false
  const [stats, setStats] = useState(null);
  const [runs, setRuns] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [limit] = useState(25);
  const [stateFilter, setStateFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoadingRuns, setIsLoadingRuns] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Handle URL change
  const navigate = useCallback((path) => {
    if (path !== window.location.pathname) {
      window.history.pushState(null, '', path);
    }
    setCurrentPath(path);
  }, []);

  // Listen for browser back / forward
  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Check auth
  const checkAuth = useCallback(async () => {
    try {
      const res = await fetch('/api/me');
      const data = await res.json();
      setIsAuthenticated(data.authenticated === true);
    } catch (e) {
      setIsAuthenticated(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Fetch aggregate stats
  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/stats');
      if (res.ok) {
        const data = await res.json();
        setStats(data);
      }
    } catch (e) {
      console.error('Failed to load stats:', e);
    }
  }, []);

  // Fetch runs
  const fetchRuns = useCallback(async (off = offset, st = stateFilter, q = searchQuery) => {
    setIsLoadingRuns(true);
    try {
      const params = new URLSearchParams({
        offset: off.toString(),
        limit: limit.toString(),
        state: st,
        q: q
      });
      const res = await fetch(`/api/runs?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setRuns(data.runs || []);
        setTotal(data.total || 0);
      }
    } catch (e) {
      console.error('Failed to load runs:', e);
    } finally {
      setIsLoadingRuns(false);
    }
  }, [offset, limit, stateFilter, searchQuery]);

  // Load data when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      fetchStats();
      fetchRuns(offset, stateFilter, searchQuery);
    }
  }, [isAuthenticated, fetchStats, fetchRuns, offset, stateFilter, searchQuery]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await Promise.all([fetchStats(), fetchRuns(offset, stateFilter, searchQuery)]);
    setTimeout(() => setIsRefreshing(false), 600);
  };

  const handleLogout = async () => {
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch (e) {
      // ignore
    }
    setIsAuthenticated(false);
  };

  const handleStateChange = (st) => {
    setStateFilter(st);
    setOffset(0);
  };

  const handleSearchChange = (q) => {
    setSearchQuery(q);
    setOffset(0);
  };

  const handlePageChange = (newOffset) => {
    setOffset(newOffset);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // 1. Checking Auth State
  if (isAuthenticated === null) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center">
        <Loader2 className="w-8 h-8 text-emerald-400 animate-spin mb-3" />
        <p className="text-xs font-semibold text-slate-400">Authenticating session...</p>
      </div>
    );
  }

  // 2. Unauthenticated -> Login View
  if (!isAuthenticated) {
    return <LoginModal onLoginSuccess={() => setIsAuthenticated(true)} />;
  }

  // 3. Match /run/:taskId
  const runMatch = currentPath.match(/^\/run\/([^/?#]+)/);
  const selectedTaskId = runMatch ? decodeURIComponent(runMatch[1]) : null;

  return (
    <div className="min-h-screen bg-[#0b0f19] text-slate-100 flex flex-col selection:bg-emerald-500/30 selection:text-emerald-200">
      
      {/* Top Navigation */}
      <Navbar
        currentPath={currentPath}
        onNavigate={navigate}
        stats={stats}
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
        onLogout={handleLogout}
      />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 pt-6 pb-12">
        {selectedTaskId ? (
          <RunDetail
            taskId={selectedTaskId}
            onBack={() => navigate('/')}
          />
        ) : currentPath === '/cron' ? (
          <CronView />
        ) : (
          <RunList
            runs={runs}
            total={total}
            limit={limit}
            offset={offset}
            stateFilter={stateFilter}
            searchQuery={searchQuery}
            isLoading={isLoadingRuns}
            onStateChange={handleStateChange}
            onSearchChange={handleSearchChange}
            onPageChange={handlePageChange}
            onSelectRun={(id) => navigate(`/run/${id}`)}
          />
        )}
      </main>

      {/* Subtle Footer */}
      <footer className="border-t border-slate-900 py-6 text-center text-xs text-slate-500">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-2">
          <span>Shaula Run History Monitor · Built with React & Tailwind CSS</span>
          <span className="font-mono text-[11px] text-slate-600">DevOps Observability Hub</span>
        </div>
      </footer>

    </div>
  );
}
