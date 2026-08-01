import { useState, useRef, useEffect } from 'react';
import { Send, User, Bot, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

export const Chat = () => {
  const initialMessages: Message[] = [
    { id: '1', role: 'assistant', content: 'Hello! Ask me anything about your course materials.' }
  ];
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [chatHistory, setChatHistory] = useState<Message[]>(initialMessages);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [chatHistory]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: Message = { id: Date.now().toString(), role: 'user', content: input };
    setChatHistory(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      // Calls the Ask endpoint from coursellm backend
      const response = await apiClient.post('/ask/', {
        query: userMessage.content,
        chat_id: 'default-session' // Simplified for UI
      });
      
      const assistantMessage: Message = { 
        id: (Date.now() + 1).toString(), 
        role: 'assistant', 
        content: response.data.answer || response.data.content || "Sorry, I couldn't process that." 
      };
      setChatHistory(prev => [...prev, assistantMessage]);
    } catch (error) {
      console.error('Error asking question', error);
      const errorMessage: Message = { 
        id: (Date.now() + 1).toString(), 
        role: 'assistant', 
        content: "An error occurred while generating a response." 
      };
      setChatHistory(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)] max-w-4xl mx-auto bg-linkedin-card border border-linkedin-border rounded-lg shadow-sm overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-linkedin-border flex items-center justify-between bg-white">
        <h1 className="font-semibold text-linkedin-text text-lg">Course Assistant</h1>
        <span className="text-xs bg-green-100 text-green-800 px-2 py-1 rounded-full font-medium">Online</span>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-linkedin-bg">
        {chatHistory.map((msg) => (
          <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${msg.role === 'user' ? 'bg-linkedin-blue text-white' : 'bg-gray-200 text-linkedin-gray'}`}>
              {msg.role === 'user' ? <User size={16} /> : <Bot size={16} />}
            </div>
            <div className={`max-w-[75%] rounded-lg p-3 text-sm shadow-sm ${msg.role === 'user' ? 'bg-linkedin-blue text-white rounded-tr-none' : 'bg-white text-linkedin-text border border-linkedin-border rounded-tl-none'}`}>
              <p className="whitespace-pre-wrap">{msg.content}</p>
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-gray-200 text-linkedin-gray flex items-center justify-center shrink-0">
              <Bot size={16} />
            </div>
            <div className="bg-white border border-linkedin-border rounded-lg rounded-tl-none p-3 shadow-sm flex items-center gap-2">
              <Loader2 size={16} className="animate-spin text-linkedin-blue" />
              <span className="text-sm text-linkedin-gray">Thinking...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-linkedin-border bg-white">
        <form onSubmit={handleSend} className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question about your course..."
            className="flex-1 border border-linkedin-border rounded-full px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-linkedin-blue focus:border-transparent transition-shadow bg-linkedin-bg"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="bg-linkedin-blue text-white p-2 w-10 h-10 rounded-full flex items-center justify-center hover:bg-linkedin-dark-blue disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={18} className="ml-1" />
          </button>
        </form>
      </div>
    </div>
  );
};
