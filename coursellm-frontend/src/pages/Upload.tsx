import React, { useState } from 'react';
import { UploadCloud, FileText, CheckCircle, XCircle } from 'lucide-react';
import { apiClient } from '../api/client';

export const Upload = () => {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFile(e.target.files[0]);
      setStatus('idle');
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setStatus('uploading');
    
    const formData = new FormData();
    formData.append('file', file);
    // Assuming backend takes course_id, setting a default for UI purpose
    formData.append('course_id', '1');

    try {
      await apiClient.post('/upload/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      setStatus('success');
      setMessage('Document uploaded and processed successfully.');
      setFile(null);
    } catch (error) {
      console.error(error);
      setStatus('error');
      setMessage('Failed to upload document. Please try again.');
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-linkedin-text mb-2">Upload Resources</h1>
        <p className="text-linkedin-gray text-sm">Upload PDFs or text files to add them to your course knowledge base.</p>
      </div>

      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-8 shadow-sm text-center">
        <div className="border-2 border-dashed border-linkedin-border rounded-lg p-12 flex flex-col items-center justify-center bg-linkedin-bg transition-colors hover:bg-gray-100">
          <input
            type="file"
            id="file-upload"
            className="hidden"
            accept=".pdf,.txt"
            onChange={handleFileChange}
          />
          <label htmlFor="file-upload" className="cursor-pointer flex flex-col items-center">
            <UploadCloud size={48} className="text-linkedin-blue mb-4" />
            <span className="font-semibold text-linkedin-text text-lg">Click to browse or drag file here</span>
            <span className="text-sm text-linkedin-gray mt-1">Supports PDF, TXT (Max 10MB)</span>
          </label>
        </div>

        {file && (
          <div className="mt-6 flex items-center justify-between p-4 border border-linkedin-border rounded-md bg-white text-left">
            <div className="flex items-center gap-3">
              <FileText className="text-linkedin-blue" />
              <div>
                <p className="text-sm font-medium text-linkedin-text">{file.name}</p>
                <p className="text-xs text-linkedin-gray">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            </div>
            <button 
              onClick={() => setFile(null)}
              className="text-linkedin-gray hover:text-red-500"
            >
              <XCircle size={20} />
            </button>
          </div>
        )}

        <div className="mt-6">
          <button
            onClick={handleUpload}
            disabled={!file || status === 'uploading'}
            className="bg-linkedin-blue text-white px-6 py-2 rounded-full font-semibold text-sm hover:bg-linkedin-dark-blue disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {status === 'uploading' ? 'Uploading & Processing...' : 'Upload to Knowledge Base'}
          </button>
        </div>

        {status === 'success' && (
          <div className="mt-4 p-3 bg-green-50 text-green-700 border border-green-200 rounded-md flex items-center gap-2 justify-center text-sm">
            <CheckCircle size={18} />
            {message}
          </div>
        )}

        {status === 'error' && (
          <div className="mt-4 p-3 bg-red-50 text-red-700 border border-red-200 rounded-md flex items-center gap-2 justify-center text-sm">
            <XCircle size={18} />
            {message}
          </div>
        )}
      </div>
    </div>
  );
};
