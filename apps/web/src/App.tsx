import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { AppShell } from '@/components/AppShell'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { ChatPage } from '@/pages/ChatPage'
import { CourseDetailPage } from '@/pages/CourseDetailPage'
import { CoursesPage } from '@/pages/CoursesPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { DocumentDetailPage } from '@/pages/DocumentDetailPage'
import { DocumentsPage } from '@/pages/DocumentsPage'
import { LoginPage } from '@/pages/LoginPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { ProgressPage } from '@/pages/ProgressPage'
import { QuizPage } from '@/pages/QuizPage'
import { RecommendationsPage } from '@/pages/RecommendationsPage'
import { RegisterPage } from '@/pages/RegisterPage'
import { RoadmapPage } from '@/pages/RoadmapPage'
import { SettingsPage } from '@/pages/SettingsPage'

export function App(): ReactNode {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/courses" element={<CoursesPage />} />
        <Route path="/courses/:courseId" element={<CourseDetailPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
        <Route path="/roadmap" element={<RoadmapPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/progress" element={<ProgressPage />} />
        <Route path="/quiz" element={<QuizPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        {/* A real 404 inside the shell, so an unknown path still has navigation. */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
