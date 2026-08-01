import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../api/client';
import { BookOpen, Calendar, Clock, Star, Plus } from 'lucide-react';

interface Course {
  id: number;
  name: string;
}

export const Dashboard = () => {
  const { user } = useAuth();
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchCourses = async () => {
      try {
        const response = await apiClient.get('/courses');
        setCourses(response.data);
      } catch (err) {
        console.error("Failed to fetch courses", err);
      } finally {
        setLoading(false);
      }
    };
    fetchCourses();
  }, []);

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
            <div className="p-4 border-b border-linkedin-border flex justify-between items-center">
              <h2 className="font-semibold text-linkedin-text text-lg">Your Courses</h2>
              <Link to="/upload" className="text-sm font-semibold text-linkedin-blue flex items-center gap-1 hover:underline">
                <Plus size={16} /> New Course
              </Link>
            </div>
            <div className="p-4 flex flex-col gap-4">
              {loading ? (
                <div className="text-center py-4 text-linkedin-gray">Loading courses...</div>
              ) : courses.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-linkedin-gray mb-4">You haven't added any courses yet.</p>
                  <Link to="/upload" className="bg-linkedin-blue text-white px-4 py-2 rounded-full text-sm font-semibold hover:bg-linkedin-dark-blue transition-colors">
                    Upload your first document
                  </Link>
                </div>
              ) : (
                courses.map((course, idx) => (
                  <div key={course.id} className="flex gap-4 p-3 hover:bg-linkedin-bg rounded-md transition-colors cursor-pointer border border-transparent hover:border-linkedin-border">
                    <div className={`w-12 h-12 rounded-md flex items-center justify-center shrink-0 ${idx % 2 === 0 ? 'bg-blue-100' : 'bg-purple-100'}`}>
                      <BookOpen className={idx % 2 === 0 ? 'text-linkedin-blue' : 'text-purple-600'} />
                    </div>
                    <div className="flex-1">
                      <h3 className="font-medium text-linkedin-text">{course.name}</h3>
                      <p className="text-xs text-linkedin-gray mt-1">Course ID: {course.id}</p>
                    </div>
                  </div>
                ))
              )}
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
