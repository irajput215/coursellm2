import { useState, useEffect } from 'react';
import { Terminal, X, ChevronUp, ChevronDown, Activity } from 'lucide-react';
import { apiClient } from '../api/client';

interface PipelineEvent {
  event: string;
  [key: string]: any;
}

export const ObservabilityPanel = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleNewRequest = (e: any) => {
      setRequestId(e.detail);
      setIsOpen(true);
    };

    window.addEventListener('new-request-id', handleNewRequest);
    return () => window.removeEventListener('new-request-id', handleNewRequest);
  }, []);

  useEffect(() => {
    if (requestId && isOpen && isExpanded) {
      fetchEvents(requestId);
    }
  }, [requestId, isOpen, isExpanded]);

  const fetchEvents = async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiClient.get(`/observability/events/${id}`);
      setEvents(response.data.events || []);
    } catch (err: any) {
      if (err.response?.status !== 404) {
         setError(err.response?.data?.detail || 'Failed to fetch observability events.');
      } else {
         setEvents([]); // 404 just means no events were stored
      }
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className={`fixed bottom-4 right-4 z-[100] bg-linkedin-card border border-linkedin-border shadow-2xl rounded-lg overflow-hidden transition-all duration-300 flex flex-col ${isExpanded ? 'w-[500px] h-[600px] max-h-[80vh] max-w-[90vw]' : 'w-[200px] h-[48px]'}`}>
      <div 
        className="bg-slate-800 text-white px-4 py-3 flex items-center justify-between cursor-pointer shrink-0"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2 font-semibold text-sm">
          <Terminal size={16} className="text-green-400" />
          Debug Console
        </div>
        <div className="flex items-center gap-2 text-slate-300">
          {isExpanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          <button 
            onClick={(e) => { e.stopPropagation(); setIsOpen(false); setIsExpanded(false); }}
            className="hover:text-red-400 transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>
      
      {isExpanded && (
        <div className="p-4 flex-1 overflow-y-auto bg-slate-900 text-slate-300">
          <div className="mb-4 pb-2 border-b border-slate-700 flex justify-between items-center">
            <p className="text-xs font-mono truncate text-slate-400" title={requestId || ''}>Req ID: {requestId}</p>
            <button 
              onClick={() => requestId && fetchEvents(requestId)}
              className="text-xs bg-slate-800 hover:bg-slate-700 px-2 py-1 rounded text-slate-300 transition-colors"
            >
              Refresh
            </button>
          </div>
          
          {loading ? (
            <div className="flex items-center justify-center h-24 text-slate-400 gap-2 text-sm">
              <Activity size={16} className="animate-pulse text-green-400" /> Fetching pipeline traces...
            </div>
          ) : error ? (
            <div className="text-red-400 text-sm p-3 bg-red-900/20 rounded-md border border-red-900/50">{error}</div>
          ) : events.length === 0 ? (
            <div className="text-sm text-slate-500 italic p-4 text-center">No pipeline events found for this request. (Make an LLM API call)</div>
          ) : (
            <div className="space-y-3">
              {events.map((ev, idx) => (
                <div key={idx} className="bg-slate-800 p-3 rounded shadow-sm border border-slate-700 text-xs font-mono">
                  <div className="font-bold text-green-400 mb-2 flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500"></span>
                    {ev.event}
                  </div>
                  <pre className="text-slate-300 mt-2 whitespace-pre-wrap break-all overflow-x-auto">
                    {JSON.stringify(Object.fromEntries(Object.entries(ev).filter(([k]) => k !== 'event' && k !== 'request_id' && k !== 'trace_id' && k !== 'user_id' && k !== 'course_id')), null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
