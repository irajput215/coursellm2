import { BarChart2, TrendingUp, Award } from 'lucide-react';

export const Evaluation = () => {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-linkedin-text mb-2">Evaluation & Progress</h1>
        <p className="text-linkedin-gray text-sm">Review your past answers and understand your learning curve.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
        <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
          <div className="w-12 h-12 bg-blue-100 rounded-full flex items-center justify-center mb-4">
            <Award className="text-linkedin-blue" />
          </div>
          <h3 className="text-3xl font-bold text-linkedin-text">94%</h3>
          <p className="text-sm text-linkedin-gray mt-1">Average Score</p>
        </div>
        
        <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
          <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mb-4">
            <TrendingUp className="text-green-600" />
          </div>
          <h3 className="text-3xl font-bold text-linkedin-text">+12%</h3>
          <p className="text-sm text-linkedin-gray mt-1">Improvement</p>
        </div>

        <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
          <div className="w-12 h-12 bg-purple-100 rounded-full flex items-center justify-center mb-4">
            <BarChart2 className="text-purple-600" />
          </div>
          <h3 className="text-3xl font-bold text-linkedin-text">14</h3>
          <p className="text-sm text-linkedin-gray mt-1">Evaluations Done</p>
        </div>
      </div>

      <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm overflow-hidden mt-6">
        <div className="p-4 border-b border-linkedin-border">
          <h2 className="font-semibold text-linkedin-text">Recent Evaluations</h2>
        </div>
        <div className="divide-y divide-linkedin-border">
          {/* Mock Item 1 */}
          <div className="p-4 flex justify-between items-center hover:bg-linkedin-bg transition-colors cursor-pointer">
            <div>
              <p className="font-medium text-linkedin-text text-sm">"Explain BFS vs DFS in Graphs"</p>
              <p className="text-xs text-linkedin-gray mt-1">Course: Advanced Data Structures • Today</p>
            </div>
            <div className="bg-green-100 text-green-800 text-xs font-bold px-2 py-1 rounded-full">
              Score: 9/10
            </div>
          </div>
          {/* Mock Item 2 */}
          <div className="p-4 flex justify-between items-center hover:bg-linkedin-bg transition-colors cursor-pointer">
            <div>
              <p className="font-medium text-linkedin-text text-sm">"How does a transformer work?"</p>
              <p className="text-xs text-linkedin-gray mt-1">Course: Machine Learning Basics • 2 days ago</p>
            </div>
            <div className="bg-yellow-100 text-yellow-800 text-xs font-bold px-2 py-1 rounded-full">
              Score: 7/10
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
