import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { askTrainer } from '../services/openai'
import type { TrainingDay } from '../store/useAppStore'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  contextWorkout?: TrainingDay
}

export default function AIChat({ contextWorkout }: Props) {
  const { openaiApiKey, userProfile, trainingPlan } = useAppStore((s) => ({
    openaiApiKey: s.openaiApiKey,
    userProfile: s.userProfile,
    trainingPlan: s.trainingPlan,
  }))

  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: contextWorkout
        ? `Hi! I'm your AI cycling coach. I can answer any questions about today's ${contextWorkout.title} workout or your training in general. What would you like to know?`
        : "Hi! I'm your AI cycling coach. Ask me anything about your training plan, recovery, nutrition, or technique!",
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    if (!openaiApiKey) {
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: input },
        { role: 'assistant', content: 'Please add your OpenAI API key in Settings to chat with me.' },
      ])
      setInput('')
      return
    }
    if (!userProfile) return

    const userMsg = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }])
    setLoading(true)

    try {
      const answer = await askTrainer(userMsg, trainingPlan, userProfile, openaiApiKey)
      setMessages((prev) => [...prev, { role: 'assistant', content: answer }])
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Sorry, something went wrong. Please check your API key.' },
      ])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 flex flex-col h-96">
      <div className="px-4 py-3 border-b flex items-center gap-2">
        <Bot size={18} className="text-amber-500" />
        <span className="font-semibold text-sm text-gray-800">AI Coach Chat</span>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {msg.role === 'assistant' && (
              <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                <Bot size={14} className="text-amber-600" />
              </div>
            )}
            <div
              className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-amber-500 text-white rounded-br-sm'
                  : 'bg-gray-100 text-gray-800 rounded-bl-sm'
              }`}
            >
              {msg.content}
            </div>
            {msg.role === 'user' && (
              <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center flex-shrink-0">
                <User size={14} className="text-gray-600" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-2 justify-start">
            <div className="w-7 h-7 rounded-full bg-amber-100 flex items-center justify-center">
              <Bot size={14} className="text-amber-600" />
            </div>
            <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-2">
              <div className="flex gap-1">
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="p-3 border-t flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && sendMessage()}
          placeholder="Ask your coach..."
          className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          className="bg-amber-500 text-white rounded-xl px-3 py-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  )
}
