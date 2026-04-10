import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User, Brain, Trash2, CalendarCheck } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import { useAppStore } from '../store/useAppStore'
import { askTrainer, updateCoachMemory, MAX_CONVERSATION_HISTORY } from '../services/ai'
import type { TrainingDay, ChatMessage } from '../store/useAppStore'

interface Props {
  contextWorkout?: TrainingDay
}

export default function AIChat({ contextWorkout }: Props) {
  const {
    aiApiKey,
    aiProvider,
    userProfile,
    trainingPlan,
    chatHistory,
    coachMemory,
    addChatMessage,
    setCoachMemory,
    clearChatHistory,
    updateTrainingDay,
  } = useAppStore(
    useShallow((s) => ({
      aiApiKey: s.aiApiKey,
      aiProvider: s.aiProvider,
      userProfile: s.userProfile,
      trainingPlan: s.trainingPlan,
      chatHistory: s.chatHistory,
      coachMemory: s.coachMemory,
      addChatMessage: s.addChatMessage,
      setCoachMemory: s.setCoachMemory,
      clearChatHistory: s.clearChatHistory,
      updateTrainingDay: s.updateTrainingDay,
    }))
  )

  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showMemory, setShowMemory] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const welcomeContent = contextWorkout
    ? `Hi! I'm your AI cycling coach. I can answer any questions about today's ${contextWorkout.title} workout or your training in general. What would you like to know?`
    : "Hi! I'm your AI cycling coach. Ask me anything about your training plan, recovery, nutrition, or technique!"

  const displayMessages: ChatMessage[] =
    chatHistory.length > 0
      ? chatHistory
      : [{ role: 'assistant', content: welcomeContent, timestamp: '' }]

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatHistory])

  const sendMessage = async () => {
    if (!input.trim() || loading) return

    const userMsg = input.trim()
    setInput('')

    if (!aiApiKey) {
      addChatMessage({ role: 'user', content: userMsg, timestamp: new Date().toISOString() })
      addChatMessage({
        role: 'assistant',
        content: 'Please add your AI provider API key in Settings to chat with me.',
        timestamp: new Date().toISOString(),
      })
      return
    }
    if (!userProfile) return

    // Capture history before adding the new user message
    const recentHistory = chatHistory.slice(-MAX_CONVERSATION_HISTORY).map((m) => ({ role: m.role, content: m.content }))

    addChatMessage({ role: 'user', content: userMsg, timestamp: new Date().toISOString() })
    setLoading(true)

    try {
      const result = await askTrainer(userMsg, trainingPlan, userProfile, aiApiKey, aiProvider, {
        coachMemory,
        conversationHistory: recentHistory,
      })

      // Apply any training plan modifications the AI suggested
      let planUpdateCount = 0
      if (result.planUpdates && result.planUpdates.length > 0) {
        for (const update of result.planUpdates) {
          const { date, ...fields } = update
          updateTrainingDay(date, fields)
        }
        planUpdateCount = result.planUpdates.length
      }

      addChatMessage({
        role: 'assistant',
        content: result.response,
        timestamp: new Date().toISOString(),
        planUpdateCount: planUpdateCount > 0 ? planUpdateCount : undefined,
      })

      // Update coach memory in background (fire-and-forget)
      updateCoachMemory(coachMemory, userMsg, result.response, aiApiKey, aiProvider)
        .then((updated) => {
          if (updated && updated !== coachMemory) setCoachMemory(updated)
        })
        .catch((err) => {
          console.warn('Coach memory update failed:', err)
        })
    } catch {
      addChatMessage({
        role: 'assistant',
        content: 'Sorry, something went wrong. Please check your API key.',
        timestamp: new Date().toISOString(),
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 flex flex-col h-[28rem]">
      {/* Header */}
      <div className="px-4 py-3 border-b flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-amber-500" />
          <span className="font-semibold text-sm text-gray-800">AI Coach Chat</span>
        </div>
        <div className="flex items-center gap-1">
          {coachMemory && (
            <button
              onClick={() => setShowMemory((v) => !v)}
              title="Coach memory"
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors ${
                showMemory
                  ? 'bg-amber-100 text-amber-700'
                  : 'text-gray-400 hover:text-amber-600 hover:bg-amber-50'
              }`}
            >
              <Brain size={13} />
              Memory
            </button>
          )}
          {chatHistory.length > 0 && (
            <button
              onClick={clearChatHistory}
              title="Clear chat history"
              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Coach memory panel */}
      {showMemory && coachMemory && (
        <div className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 leading-relaxed">
          <p className="font-semibold mb-1 flex items-center gap-1">
            <Brain size={11} /> Coach's notes about you
          </p>
          <p className="whitespace-pre-wrap">{coachMemory}</p>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {displayMessages.map((msg, i) => (
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
              {msg.planUpdateCount ? (
                <>
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                  <p className="mt-2 flex items-center gap-1 text-xs font-medium text-green-700 bg-green-100 rounded-lg px-2 py-1">
                    <CalendarCheck size={12} />
                    Training plan updated:{' '}
                    {msg.planUpdateCount === 1 ? '1 day modified.' : `${msg.planUpdateCount} days modified.`}
                  </p>
                </>
              ) : (
                msg.content
              )}
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

      {/* Input */}
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
          aria-label="Send message"
          className="bg-amber-500 text-white rounded-xl px-3 py-2 hover:bg-amber-600 disabled:opacity-50 transition-colors"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  )
}
