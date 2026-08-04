import { useState, useEffect } from 'react';
import { Calendar as CalendarIcon, RefreshCw, Mail, CheckCircle, BookOpen, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';

interface Course {
  id: number;
  name: string;
}

interface SyncStatus {
  last_sync: string | null;
  status: string;
}

export const Planner = () => {
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedCourse, setSelectedCourse] = useState<string>('');
  
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  
  const [dailyBriefing, setDailyBriefing] = useState<any>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isLoadingBriefing, setIsLoadingBriefing] = useState(false);

  // 1. Fetch courses on mount
  useEffect(() => {
    const fetchCourses = async () => {
      try {
        const response = await apiClient.get('/courses');
        setCourses(response.data);
        if (response.data.length > 0) {
          setSelectedCourse(response.data[0].name);
        }
      } catch (err) {
        console.error("Failed to fetch courses", err);
      }
    };
    fetchCourses();
  }, []);

  // 2. Fetch Sync Status and Daily Briefing when course changes
  useEffect(() => {
    if (!selectedCourse) return;
    
    const fetchData = async () => {
      setIsLoadingBriefing(true);
      try {
        const [syncRes, briefingRes] = await Promise.all([
          apiClient.get('/planner/sync-status'), // Assuming this is global or user-level
          apiClient.get(`/planner/daily-briefing?course_name=${encodeURIComponent(selectedCourse)}`)
        ]);
        setSyncStatus(syncRes.data);
        setDailyBriefing(briefingRes.data);
      } catch (e) {
        console.error("Failed to fetch planner data", e);
        setDailyBriefing(null);
      } finally {
        setIsLoadingBriefing(false);
      }
    };
    
    fetchData();
  }, [selectedCourse]);

  const handleSyncEmails = async () => {
    if (!selectedCourse) return;
    setIsSyncing(true);
    try {
      await apiClient.post('/planner/process-emails', {
        course_name: selectedCourse,
        force_refresh: false
      });
      const syncRes = await apiClient.get('/planner/sync-status');
      setSyncStatus(syncRes.data);
    } catch (error) {
      console.error("Failed to sync emails", error);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleGeneratePlan = async () => {
    if (!selectedCourse) return;
    setIsGenerating(true);
    try {
      await apiClient.post('/planner/generate-study-plan', {
        course_name: selectedCourse,
        weeks: 4
      });
      // Refetch briefing after generation
      const briefingRes = await apiClient.get(`/planner/daily-briefing?course_name=${encodeURIComponent(selectedCourse)}`);
      setDailyBriefing(briefingRes.data);
    } catch (error) {
      console.error("Failed to generate study plan", error);
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header with Course Selector */}
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-linkedin-text mb-2">Study Planner</h1>
          <p className="text-linkedin-gray text-sm">Sync your emails to automatically build and track your study schedule.</p>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto bg-linkedin-bg px-3 py-2 rounded-md border border-linkedin-border">
          <BookOpen size={16} className="text-linkedin-gray" />
          <select 
            value={selectedCourse}
            onChange={(e) => setSelectedCourse(e.target.value)}
            className="flex-1 sm:w-48 appearance-none bg-transparent text-sm focus:outline-none"
          >
            {courses.length === 0 ? (
              <option value="">No courses available</option>
            ) : (
              courses.map(c => (
                <option key={c.id} value={c.name}>{c.name}</option>
              ))
            )}
          </select>
        </div>
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
              disabled={isSyncing || !selectedCourse}
              className="w-full flex justify-center items-center gap-2 bg-white border border-linkedin-blue text-linkedin-blue px-4 py-2 rounded-full font-semibold text-sm hover:bg-blue-50 disabled:opacity-50 transition-colors"
            >
              {isSyncing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              {isSyncing ? 'Syncing...' : 'Sync Emails Now'}
            </button>
          </div>
        </div>

        {/* Study Plan Overview */}
        <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-6 flex flex-col items-center text-center">
          <CalendarIcon size={32} className="text-linkedin-blue mb-4" />
          <h3 className="font-semibold text-linkedin-text mb-2">Your Daily Briefing</h3>
          
          <div className="w-full flex-1 mb-6 mt-2 overflow-y-auto max-h-48 text-left bg-linkedin-bg p-4 rounded-md border border-linkedin-border text-sm text-linkedin-gray">
            {isLoadingBriefing ? (
              <div className="flex justify-center items-center h-full">
                <Loader2 size={24} className="animate-spin text-linkedin-blue" />
              </div>
            ) : dailyBriefing ? (
              <pre className="whitespace-pre-wrap font-sans">
                {typeof dailyBriefing === 'string' ? dailyBriefing : JSON.stringify(dailyBriefing, null, 2)}
              </pre>
            ) : (
              <p className="text-center italic">No briefing available. Generate a study plan to get started.</p>
            )}
          </div>

          <button 
            onClick={handleGeneratePlan}
            disabled={isGenerating || !selectedCourse}
            className="w-full bg-linkedin-blue text-white px-6 py-2 rounded-full font-semibold text-sm hover:bg-linkedin-dark-blue disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
          >
            {isGenerating && <Loader2 size={16} className="animate-spin" />}
            {isGenerating ? 'Generating...' : 'Generate New Study Plan'}
          </button>
        </div>
      </div>
    </div>
  );
};
