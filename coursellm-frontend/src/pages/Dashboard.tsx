import { useAuth } from '../contexts/AuthContext';
import { BookOpen, Calendar, Clock, Star } from 'lucide-react';

export const Dashboard = () => {
  const { user } = useAuth();

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Welcome Banner */}
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-linkedin-text mb-2">Welcome back, {user?.full_name || 'Student'}!</h1>
        <p className="text-linkedin-gray text-sm">Here is a quick overview of your learning progress.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Left Column (Main Content) */}
        <div className="md:col-span-2 space-y-6">
          
          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm overflow-hidden">
            <div className="p-4 border-b border-linkedin-border">
              <h2 className="font-semibold text-linkedin-text text-lg">Recent Courses</h2>
            </div>
            <div className="p-4 flex flex-col gap-4">
              {/* Mock Course Items */}
              <div className="flex gap-4 p-3 hover:bg-linkedin-bg rounded-md transition-colors cursor-pointer border border-transparent hover:border-linkedin-border">
                <div className="w-12 h-12 bg-blue-100 rounded-md flex items-center justify-center shrink-0">
                  <BookOpen className="text-linkedin-blue" />
                </div>
                <div>
                  <h3 className="font-medium text-linkedin-text">Machine Learning Basics</h3>
                  <p className="text-xs text-linkedin-gray mt-1">Uploaded 2 documents • Last active today</p>
                </div>
              </div>
              
              <div className="flex gap-4 p-3 hover:bg-linkedin-bg rounded-md transition-colors cursor-pointer border border-transparent hover:border-linkedin-border">
                <div className="w-12 h-12 bg-purple-100 rounded-md flex items-center justify-center shrink-0">
                  <BookOpen className="text-purple-600" />
                </div>
                <div>
                  <h3 className="font-medium text-linkedin-text">Advanced Data Structures</h3>
                  <p className="text-xs text-linkedin-gray mt-1">Uploaded 5 documents • Last active yesterday</p>
                </div>
              </div>
            </div>
            <div className="p-3 border-t border-linkedin-border bg-linkedin-bg text-center">
              <button className="text-sm font-semibold text-linkedin-gray hover:text-linkedin-text">View all courses</button>
            </div>
          </div>

        </div>

        {/* Right Column (Sidebar/Widgets) */}
        <div className="space-y-6">
          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-4">
            <h2 className="font-semibold text-linkedin-text mb-4">Upcoming Deadlines</h2>
            <ul className="space-y-3">
              <li className="flex gap-3 text-sm">
                <Calendar className="text-linkedin-gray shrink-0" size={18} />
                <div>
                  <p className="font-medium text-linkedin-text">Submit ML Assignment</p>
                  <p className="text-xs text-red-500 mt-0.5 flex items-center gap-1"><Clock size={12} /> Tomorrow at 11:59 PM</p>
                </div>
              </li>
              <li className="flex gap-3 text-sm">
                <Calendar className="text-linkedin-gray shrink-0" size={18} />
                <div>
                  <p className="font-medium text-linkedin-text">Data Structures Midterm</p>
                  <p className="text-xs text-linkedin-gray mt-0.5">Next Friday</p>
                </div>
              </li>
            </ul>
          </div>

          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-4">
            <h2 className="font-semibold text-linkedin-text mb-4">Study Stats</h2>
            <div className="flex justify-between items-center text-sm mb-2">
              <span className="text-linkedin-gray">Questions Asked</span>
              <span className="font-bold text-linkedin-text">142</span>
            </div>
            <div className="flex justify-between items-center text-sm mb-2">
              <span className="text-linkedin-gray">Docs Uploaded</span>
              <span className="font-bold text-linkedin-text">7</span>
            </div>
            <div className="flex justify-between items-center text-sm">
              <span className="text-linkedin-gray">Avg Evaluation Score</span>
              <span className="font-bold text-green-600 flex items-center gap-1"><Star size={14} className="fill-green-600"/> 94%</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
