import { useState, useEffect } from 'react';
import { Calendar as CalendarIcon, RefreshCw, Mail, CheckCircle } from 'lucide-react';
import { apiClient } from '../api/client';

interface SyncStatus {
  last_sync: string | null;
  status: string;
}

export const Planner = () => {
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    fetchSyncStatus();
  }, []);

  const fetchSyncStatus = async () => {
    try {
      const res = await apiClient.get('/planner/sync-status');
      setSyncStatus(res.data);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSyncEmails = async () => {
    setIsSyncing(true);
    try {
      await apiClient.post('/planner/process-emails');
      await fetchSyncStatus();
    } catch (error) {
      console.error(error);
    } finally {
      setIsSyncing(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-linkedin-text mb-2">Study Planner</h1>
          <p className="text-linkedin-gray text-sm">Sync your emails to automatically build and track your study schedule.</p>
        </div>
        <CalendarIcon size={48} className="text-linkedin-gray opacity-50" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Sync Status Card */}
        <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-6">
          <div className="flex items-center gap-3 mb-4">
            <Mail className="text-linkedin-blue" />
            <h2 className="font-semibold text-linkedin-text text-lg">Email Integration</h2>
          </div>
          
          <div className="space-y-4">
            <div className="flex justify-between items-center text-sm border-b border-linkedin-border pb-3">
              <span className="text-linkedin-gray">Status</span>
              <span className="font-medium flex items-center gap-1 text-green-600">
                <CheckCircle size={16} /> {syncStatus?.status || 'Connected'}
              </span>
            </div>
            <div className="flex justify-between items-center text-sm pb-3">
              <span className="text-linkedin-gray">Last Synced</span>
              <span className="font-medium text-linkedin-text">
                {syncStatus?.last_sync ? new Date(syncStatus.last_sync).toLocaleString() : 'Never'}
              </span>
            </div>
            
            <button
              onClick={handleSyncEmails}
              disabled={isSyncing}
              className="w-full flex justify-center items-center gap-2 bg-white border border-linkedin-blue text-linkedin-blue px-4 py-2 rounded-full font-semibold text-sm hover:bg-blue-50 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={16} className={isSyncing ? 'animate-spin' : ''} />
              {isSyncing ? 'Syncing...' : 'Sync Emails Now'}
            </button>
          </div>
        </div>

        {/* Study Plan Overview */}
        <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-6 flex flex-col justify-center items-center text-center">
          <CalendarIcon size={32} className="text-linkedin-gray mb-4" />
          <h3 className="font-semibold text-linkedin-text mb-2">Your Daily Briefing</h3>
          <p className="text-sm text-linkedin-gray mb-4">You have 3 tasks scheduled for today based on your recent syllabus updates.</p>
          <button className="bg-linkedin-blue text-white px-6 py-2 rounded-full font-semibold text-sm hover:bg-linkedin-dark-blue transition-colors">
            Generate New Study Plan
          </button>
        </div>
      </div>
    </div>
  );
};
