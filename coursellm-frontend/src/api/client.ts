import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Add a response interceptor to capture X-Request-ID
apiClient.interceptors.response.use(
  (response) => {
    const requestId = response.headers['x-request-id'];
    if (requestId) {
      window.dispatchEvent(new CustomEvent('new-request-id', { detail: requestId }));
    }
    return response;
  },
  (error) => {
    if (error.response && error.response.headers) {
      const requestId = error.response.headers['x-request-id'];
      if (requestId) {
        window.dispatchEvent(new CustomEvent('new-request-id', { detail: requestId }));
      }
    }
    return Promise.reject(error);
  }
);
