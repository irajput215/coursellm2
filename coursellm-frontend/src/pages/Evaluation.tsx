import React, { useState, useEffect } from 'react';
import { BarChart2, TrendingUp, Award, BookOpen, Send, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';

interface Course {
  id: number;
  name: string;
}

interface EvaluationProgress {
  average_score: number;
  evaluations_count: number;
  improvement_percentage: number;
}

interface Submission {
  id: string;
  question: string;
  student_answer: string;
  score: number;
  submitted_at: string;
}

export const Evaluation = () => {
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedCourse, setSelectedCourse] = useState<string>('');
  
  const [progress, setProgress] = useState<EvaluationProgress | null>(null);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');

  const [examQuestions, setExamQuestions] = useState<string[]>([]);
  const [isGeneratingQuestions, setIsGeneratingQuestions] = useState(false);

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

  // 2. Fetch evaluation data when course changes
  useEffect(() => {
    if (!selectedCourse) return;
    
    const fetchEvaluationData = async () => {
      setIsLoading(true);
      try {
        const [progressRes, submissionsRes] = await Promise.all([
          apiClient.get(`/evaluation/progress?course_name=${encodeURIComponent(selectedCourse)}`),
          apiClient.get(`/evaluation/submissions?course_name=${encodeURIComponent(selectedCourse)}`)
        ]);
        setProgress(progressRes.data);
        // Depending on backend, submissionsRes.data might be { submissions: [...] }
        setSubmissions(submissionsRes.data.submissions || []);
      } catch (err) {
        console.error("Failed to fetch evaluation data", err);
        setSubmissions([]);
      } finally {
        setIsLoading(false);
      }
    };
    
    fetchEvaluationData();
    
    // Load cached questions for this course if they exist
    const cachedQuestions = localStorage.getItem(`exam_questions_${selectedCourse}`);
    if (cachedQuestions) {
      try {
        setExamQuestions(JSON.parse(cachedQuestions));
      } catch (e) {
        setExamQuestions([]);
      }
    } else {
      setExamQuestions([]);
    }
  }, [selectedCourse]);

  const handleGenerateQuestions = async () => {
    if (!selectedCourse || isGeneratingQuestions) return;
    
    setIsGeneratingQuestions(true);
    try {
      const response = await apiClient.get(`/evaluation/exam-questions?course_name=${encodeURIComponent(selectedCourse)}`);
      const newQuestions = response.data.questions || [];
      setExamQuestions(newQuestions);
      localStorage.setItem(`exam_questions_${selectedCourse}`, JSON.stringify(newQuestions));
    } catch (err) {
      console.error("Failed to generate exam questions", err);
    } finally {
      setIsGeneratingQuestions(false);
    }
  };

  const handleSubmitAnswer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCourse || !question.trim() || !answer.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await apiClient.post('/evaluation/grade', {
        question: question,
        student_answer: answer,
        course_name: selectedCourse
      });
      setQuestion('');
      setAnswer('');
      // Refresh submissions
      const submissionsRes = await apiClient.get(`/evaluation/submissions?course_name=${encodeURIComponent(selectedCourse)}`);
      setSubmissions(submissionsRes.data.submissions || []);
      // Refresh progress
      const progressRes = await apiClient.get(`/evaluation/progress?course_name=${encodeURIComponent(selectedCourse)}`);
      setProgress(progressRes.data);
    } catch (err) {
      console.error("Failed to submit answer for grading", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header with Course Selector */}
      <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-linkedin-text mb-2">Evaluation & Progress</h1>
          <p className="text-linkedin-gray text-sm">Review your past answers and understand your learning curve.</p>
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

      {isLoading ? (
        <div className="flex justify-center p-12">
          <Loader2 className="animate-spin text-linkedin-blue" size={32} />
        </div>
      ) : (
        <>
          {/* Stats Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
              <div className="w-12 h-12 bg-blue-100 rounded-full flex items-center justify-center mb-4">
                <Award className="text-linkedin-blue" />
              </div>
              <h3 className="text-3xl font-bold text-linkedin-text">
                {progress?.average_score !== undefined ? `${progress.average_score}%` : '--'}
              </h3>
              <p className="text-sm text-linkedin-gray mt-1">Average Score</p>
            </div>
            
            <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
              <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mb-4">
                <TrendingUp className="text-green-600" />
              </div>
              <h3 className="text-3xl font-bold text-linkedin-text">
                {progress?.improvement_percentage !== undefined ? `${progress.improvement_percentage > 0 ? '+' : ''}${progress.improvement_percentage}%` : '--'}
              </h3>
              <p className="text-sm text-linkedin-gray mt-1">Improvement</p>
            </div>

            <div className="bg-linkedin-card border border-linkedin-border rounded-lg p-6 shadow-sm flex flex-col items-center justify-center text-center">
              <div className="w-12 h-12 bg-purple-100 rounded-full flex items-center justify-center mb-4">
                <BarChart2 className="text-purple-600" />
              </div>
              <h3 className="text-3xl font-bold text-linkedin-text">
                {progress?.evaluations_count !== undefined ? progress.evaluations_count : '--'}
              </h3>
              <p className="text-sm text-linkedin-gray mt-1">Evaluations Done</p>
            </div>
          </div>

          {/* Generate Exam Questions */}
          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-6 mt-6">
            <div className="flex justify-between items-center">
              <h2 className="font-semibold text-linkedin-text">Practice for the Exam</h2>
              <button
                onClick={handleGenerateQuestions}
                disabled={!selectedCourse || isGeneratingQuestions}
                className="bg-linkedin-bg text-linkedin-blue border border-linkedin-blue px-4 py-2 rounded-full font-semibold text-sm hover:bg-blue-50 disabled:opacity-50 flex items-center gap-2"
              >
                {isGeneratingQuestions ? <Loader2 size={16} className="animate-spin" /> : <BookOpen size={16} />}
                Generate 5 Questions
              </button>
            </div>
            
            {examQuestions.length > 0 && (
              <div className="space-y-3 mt-6">
                <p className="text-sm text-linkedin-gray mb-2">Click a question below to answer it in the form.</p>
                {examQuestions.map((q, idx) => (
                  <div 
                    key={idx}
                    onClick={() => {
                      setQuestion(q);
                      document.getElementById('answer-textarea')?.focus();
                    }}
                    className="p-3 border border-linkedin-border rounded-md hover:border-linkedin-blue cursor-pointer transition-colors text-sm text-linkedin-text group"
                  >
                    <span className="font-semibold text-linkedin-blue mr-2 group-hover:underline">Q{idx + 1}:</span> {q}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Submit New Answer */}
          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm p-6 mt-6">
            <h2 className="font-semibold text-linkedin-text mb-4">Submit Answer for Grading</h2>
            <form onSubmit={handleSubmitAnswer} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-linkedin-text mb-1">Question</label>
                <input 
                  type="text" 
                  value={question}
                  onChange={e => setQuestion(e.target.value)}
                  placeholder="What is the main topic of chapter 1?"
                  className="w-full appearance-none rounded-md border border-linkedin-border px-3 py-2 text-sm focus:border-linkedin-blue focus:outline-none focus:ring-1 focus:ring-linkedin-blue"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-linkedin-text mb-1">Your Answer</label>
                <textarea 
                  id="answer-textarea"
                  value={answer}
                  onChange={e => setAnswer(e.target.value)}
                  placeholder="Type your detailed answer here..."
                  rows={4}
                  className="w-full appearance-none rounded-md border border-linkedin-border px-3 py-2 text-sm focus:border-linkedin-blue focus:outline-none focus:ring-1 focus:ring-linkedin-blue"
                />
              </div>
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={!selectedCourse || !question.trim() || !answer.trim() || isSubmitting}
                  className="bg-linkedin-blue text-white px-6 py-2 rounded-full font-semibold text-sm hover:bg-linkedin-dark-blue disabled:opacity-50 flex items-center gap-2"
                >
                  {isSubmitting ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                  {isSubmitting ? 'Grading...' : 'Grade My Answer'}
                </button>
              </div>
            </form>
          </div>

          {/* Submissions List */}
          <div className="bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm overflow-hidden mt-6">
            <div className="p-4 border-b border-linkedin-border">
              <h2 className="font-semibold text-linkedin-text">Recent Evaluations</h2>
            </div>
            <div className="divide-y divide-linkedin-border">
              {submissions.length === 0 ? (
                <div className="p-8 text-center text-linkedin-gray text-sm">
                  No evaluations found for this course yet.
                </div>
              ) : (
                submissions.map((sub, idx) => (
                  <div key={idx} className="p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center hover:bg-linkedin-bg transition-colors gap-4">
                    <div>
                      <p className="font-medium text-linkedin-text text-sm">Q: {sub.question}</p>
                      <p className="text-sm text-linkedin-gray mt-1 italic">A: "{sub.student_answer}..."</p>
                      <p className="text-xs text-linkedin-gray mt-2">
                        {new Date(sub.submitted_at).toLocaleString()}
                      </p>
                    </div>
                    <div className={`text-xs font-bold px-3 py-1 rounded-full shrink-0 ${
                      sub.score >= 80 ? 'bg-green-100 text-green-800' :
                      sub.score >= 60 ? 'bg-yellow-100 text-yellow-800' :
                      'bg-red-100 text-red-800'
                    }`}>
                      Score: {sub.score}%
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};
