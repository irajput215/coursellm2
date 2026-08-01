import { Link } from 'react-router-dom';
import { BookOpen, Search, User } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

export const Navbar = () => {
  const { user, logout } = useAuth();

  return (
    <nav className="bg-linkedin-card border-b border-linkedin-border h-14 fixed w-full z-10 top-0 left-0 flex items-center px-4 md:px-8 justify-between shadow-sm">
      <div className="flex items-center gap-2">
        <Link to="/" className="flex items-center gap-2 text-linkedin-blue">
          <BookOpen size={28} className="fill-linkedin-blue text-white" />
          <span className="text-xl font-bold tracking-tight">CourseLLM</span>
        </Link>
        <div className="hidden md:flex ml-4 bg-linkedin-bg rounded-md items-center px-2 py-1.5 focus-within:ring-2 focus-within:ring-linkedin-blue w-64 transition-all">
          <Search size={18} className="text-linkedin-gray" />
          <input 
            type="text" 
            placeholder="Search resources..." 
            className="bg-transparent border-none outline-none text-sm ml-2 w-full text-linkedin-text placeholder-linkedin-gray"
          />
        </div>
      </div>
      
      <div className="flex items-center gap-4">
        {user ? (
          <div className="flex items-center gap-4 text-sm text-linkedin-gray">
            <span className="hidden md:inline-block font-medium">{user.full_name}</span>
            <button onClick={logout} className="hover:text-linkedin-text transition-colors">Sign Out</button>
          </div>
        ) : (
          <Link to="/login" className="flex items-center flex-col text-linkedin-gray hover:text-linkedin-text transition-colors">
            <User size={24} />
            <span className="text-xs mt-1">Me</span>
          </Link>
        )}
      </div>
    </nav>
  );
};
