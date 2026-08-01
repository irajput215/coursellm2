import { Link, useLocation } from 'react-router-dom';
import { Home, MessageSquare, UploadCloud, Calendar, BarChart2 } from 'lucide-react';

const navItems = [
  { name: 'Dashboard', path: '/', icon: Home },
  { name: 'Chat (Ask)', path: '/chat', icon: MessageSquare },
  { name: 'Upload', path: '/upload', icon: UploadCloud },
  { name: 'Study Planner', path: '/planner', icon: Calendar },
  { name: 'Evaluation', path: '/evaluation', icon: BarChart2 },
];

export const Sidebar = () => {
  const location = useLocation();

  return (
    <aside className="w-64 bg-transparent hidden md:block pt-4 pl-4 shrink-0">
      <div className="bg-linkedin-card rounded-lg border border-linkedin-border overflow-hidden shadow-sm sticky top-20">
        <div className="p-4 border-b border-linkedin-border">
          <h2 className="text-sm font-semibold text-linkedin-text">Navigation</h2>
        </div>
        <ul className="flex flex-col py-2">
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            const Icon = item.icon;
            return (
              <li key={item.path}>
                <Link
                  to={item.path}
                  className={`flex items-center gap-3 px-4 py-3 text-sm transition-colors ${
                    isActive
                      ? 'bg-linkedin-bg text-linkedin-blue border-l-4 border-linkedin-blue font-semibold font-sans'
                      : 'text-linkedin-gray hover:bg-linkedin-bg hover:text-linkedin-text border-l-4 border-transparent'
                  }`}
                >
                  <Icon size={20} className={isActive ? 'text-linkedin-blue' : 'text-linkedin-gray'} />
                  {item.name}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
};
