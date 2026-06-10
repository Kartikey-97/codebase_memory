import { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './styles.module.css';
import { IS_MOCK, runMockChat } from '../../mock';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

function ChatPanel({ repoId }) {
  const [messages, setMessages] = useState([]); // { role, content, sources? }
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const abortRef = useRef(null);

  // ── Scroll to bottom on new messages ──────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, thinking]);

  // ── Auto-expand textarea ──────────────────────────────────────
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const lineHeight = 20;
    const maxHeight = lineHeight * 5;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, []);

  useEffect(() => autoResize(), [input, autoResize]);

  // ── Build history array from messages ─────────────────────────
  const buildHistory = () =>
    messages.map((m) => ({ role: m.role, content: m.content }));

  // ── Handle submit ─────────────────────────────────────────────
  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || thinking || !repoId) return;

    const userMsg = { role: 'user', content: trimmed };
    const history = buildHistory();
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setThinking(true);

    if (IS_MOCK) {
      const cancel = runMockChat((evt) => handleSSEEvent(evt));
      abortRef.current = { abort: cancel };
      return;
    }

    // Real SSE fetch.
    const controller = new AbortController();
    abortRef.current = controller;

    fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_id: repoId,
        message: trimmed,
        history,
      }),
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.body.getReader();
      })
      .then(async (reader) => {
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data:')) {
              const raw = line.slice(5).trim();
              if (!raw) continue;
              try {
                handleSSEEvent(JSON.parse(raw));
              } catch {
                /* skip */
              }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setMessages((prev) => [
            ...prev,
            { role: 'agent', content: `Error: ${err.message}` },
          ]);
          setThinking(false);
        }
      });
  };

  const handleSSEEvent = (evt) => {
    const type = evt.type;

    if (type === 'message') {
      setMessages((prev) => [
        ...prev,
        {
          role: 'agent',
          content: evt.content || evt.message || '',
          sources: evt.sources || [],
        },
      ]);
      setThinking(false);
      return;
    }

    if (type === 'error') {
      setMessages((prev) => [
        ...prev,
        { role: 'agent', content: `Error: ${evt.message}` },
      ]);
      setThinking(false);
      return;
    }

    if (type === 'done') {
      setThinking(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // ── No-repo placeholder ───────────────────────────────────────
  if (!repoId) {
    return (
      <div className={styles.container}>
        <div className={styles.empty} style={{ padding: '24px', textAlign: 'center' }}>
          Connect a repository to start chatting.
        </div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      {/* Message list */}
      <div className={styles.messageList}>
        {messages.length === 0 && !thinking && (
          <div className={styles.empty}>
            Ask questions about your indexed repository.
          </div>
        )}

        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={
              msg.role === 'user' ? styles.userMsg : styles.agentMsg
            }
          >
            {msg.role === 'user' ? (
              <div className={styles.userBubble}>{msg.content}</div>
            ) : (
              <div className={styles.agentContent}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    code({ className, children, ...props }) {
                      const isBlock = className?.startsWith('language-');
                      if (isBlock) {
                        return (
                          <pre className={styles.codeBlock}>
                            <code className={className} {...props}>
                              {children}
                            </code>
                          </pre>
                        );
                      }
                      return (
                        <code className={styles.inlineCode} {...props}>
                          {children}
                        </code>
                      );
                    },
                  }}
                >
                  {msg.content}
                </ReactMarkdown>

                {msg.sources && msg.sources.length > 0 && (
                  <div className={styles.sources}>
                    <span className={styles.sourcesLabel}>Referenced files:</span>
                    {msg.sources.map((src, i) => (
                      <span key={i} className={styles.sourcePath}>
                        {src}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {thinking && (
          <div className={styles.agentMsg}>
            <div className={styles.thinkingPlaceholder}>
              <span className={styles.dot} />
              <span className={styles.dot} />
              <span className={styles.dot} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className={styles.inputArea}>
        <textarea
          ref={textareaRef}
          id="chat-input"
          className={styles.textarea}
          placeholder="Ask about your codebase…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={thinking}
        />
        <button
          id="chat-send"
          className={styles.sendBtn}
          onClick={handleSubmit}
          disabled={thinking || !input.trim()}
          aria-label="Send message"
        >
          ↑
        </button>
      </div>
    </div>
  );
}

export default ChatPanel;
