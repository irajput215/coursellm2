import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../api/client';
import { BookOpen } from 'lucide-react';

export const Login = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isRegistering, setIsRegistering] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      if (isRegistering) {
        await apiClient.post('/auth/register', { 
          username: email, 
          email: email, 
          password: password,
          full_name: email.split('@')[0]
        });
      }
      // Assuming OAuth2 password flow: username & password as form data
      const formData = new URLSearchParams();
      formData.append('username', email);
      formData.append('password', password);
      
      const response = await apiClient.post('/auth/token', formData, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      
      await login(response.data.access_token);
      navigate('/');
    } catch (err: any) {
      let errorMsg = 'Authentication failed. Please try again.';
      if (err.response?.data?.detail) {
        if (typeof err.response.data.detail === 'string') {
          errorMsg = err.response.data.detail;
        } else if (Array.isArray(err.response.data.detail)) {
          errorMsg = err.response.data.detail.map((e: any) => e.msg).join(', ');
        }
      }
      setError(errorMsg);
    }
  };

  return (
    <div className="min-h-screen bg-linkedin-bg flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md flex flex-col items-center">
        <BookOpen size={48} className="text-linkedin-blue mb-4" />
        <h2 className="mt-6 text-center text-3xl font-bold tracking-tight text-linkedin-text">
          {isRegistering ? 'Create your account' : 'Sign in to CourseLLM'}
        </h2>
        <p className="mt-2 text-center text-sm text-linkedin-gray">
          Stay on top of your courses and learning progress
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-linkedin-card py-8 px-4 shadow sm:rounded-lg sm:px-10 border border-linkedin-border">
          <form className="space-y-6" onSubmit={handleSubmit}>
            {error && (
              <div className="bg-red-50 text-red-600 p-3 rounded-md text-sm border border-red-200">
                {error}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-linkedin-text">Email address</label>
              <div className="mt-1">
                <input
                  type="email"
                  required
                  className="block w-full appearance-none rounded-md border border-linkedin-border px-3 py-2 placeholder-gray-400 shadow-sm focus:border-linkedin-blue focus:outline-none focus:ring-linkedin-blue sm:text-sm"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-linkedin-text">Password</label>
              <div className="mt-1">
                <input
                  type="password"
                  required
                  className="block w-full appearance-none rounded-md border border-linkedin-border px-3 py-2 placeholder-gray-400 shadow-sm focus:border-linkedin-blue focus:outline-none focus:ring-linkedin-blue sm:text-sm"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>

            <div>
              <button
                type="submit"
                className="flex w-full justify-center rounded-full border border-transparent bg-linkedin-blue py-2 px-4 text-sm font-medium text-white shadow-sm hover:bg-linkedin-dark-blue focus:outline-none focus:ring-2 focus:ring-linkedin-blue focus:ring-offset-2 transition-colors"
              >
                {isRegistering ? 'Register' : 'Sign In'}
              </button>
            </div>
          </form>

          <div className="mt-6">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-linkedin-border" />
              </div>
              <div className="relative flex justify-center text-sm">
                <span className="bg-linkedin-card px-2 text-linkedin-gray">
                  {isRegistering ? 'Already on CourseLLM?' : 'New to CourseLLM?'}
                </span>
              </div>
            </div>

            <div className="mt-6 text-center">
              <button
                onClick={() => setIsRegistering(!isRegistering)}
                className="text-linkedin-blue font-semibold hover:underline"
              >
                {isRegistering ? 'Sign in instead' : 'Register'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
