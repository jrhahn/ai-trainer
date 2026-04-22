import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User, Brain, Trash2, CalendarCheck, BookOpen } from 'lucide-react'
import { useShallow } from 'zustand/shallow'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import { useAppStore } from '../store/useAppStore'
import { askTrainer } from '../services/ai'
import { clearChatHistoryRemote, fetchCoachMemory } from '../services/user'
import type { TrainingDay, ChatMessage } from '../store/useAppStore'

// Render headings as plain paragraphs so the chat uses a uniform font size
const MARKDOWN_COMPONENTS: Components = {
  h1: 'p',
  h2: 'p',
  h3: 'p',
  h4: 'p',
  h5: 'p',
  h6: 'p',
}

interface Props {
  contextWorkout?: TrainingDay
  className?: string
}

export default function AIChat({ contextWorkout, className }: Props) {
  const {
    authToken,
    userProfile,
    chatHistory,
    coachMemory,
    addChatMessage,
    setCoachMemory,
    clearChatHistory,
    updateTrainingDay,
  } = useAppStore(
    useShallow((s) => ({
      authToken: s.authToken,
      userProfile: s.userProfile,
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
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-grow the textarea as the user types
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 128)}px`
    }
  }, [input])

  const welcomeContent = contextWorkout
    ? `Hey! 👋 Great to see you here. I'm your coach and I'm genuinely excited to help you nail today's ${contextWorkout.title} session. Got any questions about it, or anything else on your mind?`
    : "Hey! 👋 I'm your coach — think of me as that friend who's always happy to chat training. Ask me anything: your plan, recovery, nutrition, technique… I'm here for it! 😊"

  const displayMessages: ChatMessage[] =
    chatHistory.length > 0
      ? chatHistory
      : [{ role: 'assistant', content: welcomeContent, timestamp: '' }]

  const sendMessage = async () => {
    if (!input.trim() || loading) return

    const userMsg = input.trim()
    const timestamp = new Date().toISOString()
    setInput('')

    if (!userProfile || !authToken) return

    addChatMessage({ role: 'user', content: userMsg, timestamp })
    setLoading(true)

    try {
      const result = await askTrainer(userMsg, authToken, { contextWorkout })

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
        sources: result.sources?.length ? result.sources : undefined,
      })

      // Re-sync coach memory from server (backend updated it inside ask_trainer)
      fetchCoachMemory(authToken)
        .then((memory) => setCoachMemory(memory))
        .catch((err) => console.warn('Failed to re-fetch coach memory:', err))
    } catch {
      addChatMessage({
        role: 'assistant',
        content: 'Sorry, something went wrong.',
        timestamp: new Date().toISOString(),
      })
    } finally {
      setLoading(false)
    }
  }

  const handleClearChatHistory = async () => {
    if (!authToken) return
    clearChatHistory()
    try {
      await clearChatHistoryRemote(authToken)
    } catch {
      // keep local state cleared
    }
  }

  return (
    <div className={`bg-white rounded-xl border border-gray-100 flex flex-col ${className ?? 'shadow-sm h-[28rem]'}`}>
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
              onClick={() => void handleClearChatHistory()}
              title="Clear chat history"
              className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="p-3 border-b flex gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void sendMessage()
            }
          }}
          placeholder="Ask your coach..."
          aria-label="Message to coach"
          rows={1}
          className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500 resize-none"
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

      {/* Coach memory panel */}
      {showMemory && coachMemory && (
        <div className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 leading-relaxed">
          <p className="font-semibold mb-1 flex items-center gap-1">
            <Brain size={11} /> Coach's notes about you
          </p>
          <p className="whitespace-pre-wrap">{coachMemory}</p>
        </div>
      )}

      {/* Messages — flex-col-reverse renders newest at the top so no scrolling needed */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col-reverse gap-3">
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
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown components={MARKDOWN_COMPONENTS}>{msg.content}</ReactMarkdown>
                  ) : (
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  )}
                  <p className="mt-2 flex items-center gap-1 text-xs font-medium text-green-700 bg-green-100 rounded-lg px-2 py-1">
                    <CalendarCheck size={12} />
                    Training plan updated:{' '}
                    {msg.planUpdateCount === 1 ? '1 day modified.' : `${msg.planUpdateCount} days modified.`}
                  </p>
                </>
              ) : msg.role === 'assistant' ? (
                <ReactMarkdown components={MARKDOWN_COMPONENTS}>{msg.content}</ReactMarkdown>
              ) : (
                <span className="whitespace-pre-wrap">{msg.content}</span>
              )}
              {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 border-t border-gray-200 pt-2">
                  <p className="flex items-center gap-1 text-xs font-semibold text-gray-500 mb-1">
                    <BookOpen size={11} />
                    Sources
                  </p>
                  <ul className="space-y-0.5">
                    {msg.sources.map((source, idx) => (
                      <li key={idx} className="text-xs text-gray-500">
                        {source.url ? (
                          <a
                            href={source.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-amber-600 underline"
                          >
                            {source.title}
                          </a>
                        ) : (
                          source.title
                        )}
                        {source.doi && (
                          <span className="ml-1 text-gray-400">· DOI: {source.doi}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
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
      </div>

    </div>
  )
}
