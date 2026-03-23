import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Send, User, Bot, Loader2, RotateCcw } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { sendChatMessage } from '../services/api';
import type { ChatMessage as ApiChatMessage } from '../services/api';

// localStorage key where the active session UUID is persisted across page refreshes
const SESSION_STORAGE_KEY = 'nda_chat_session_id';

const WELCOME_MESSAGE = "Hello! I am the NDA Portfolio RAG Assistant. I have access to the latest NDA MPPR and EAC reference data. How can I help you analyze the portfolio today?";

interface ChatMessage extends ApiChatMessage {
    id: string;
}

const initialMessages = (): ChatMessage[] => [
    { id: '1', role: 'assistant', content: WELCOME_MESSAGE },
];

const ChatView = () => {
    const [messages, setMessages]     = useState<ChatMessage[]>(initialMessages);
    const [input, setInput]           = useState('');
    const [isLoading, setIsLoading]   = useState(false);

    // Session ID is the single piece of state the frontend needs to persist.
    // On mount we read from localStorage so conversations survive page refreshes.
    // On first server response we write the server-assigned UUID back to localStorage.
    const [sessionId, setSessionId] = useState<string | null>(
        () => localStorage.getItem(SESSION_STORAGE_KEY)
    );

    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isLoading]);

    /**
     * Reset the conversation: clear visible messages, wipe the stored session ID,
     * and let the next message create a fresh server-side session.
     */
    const handleNewConversation = useCallback(() => {
        localStorage.removeItem(SESSION_STORAGE_KEY);
        setSessionId(null);
        setMessages(initialMessages());
        setInput('');
    }, []);

    const handleSend = async () => {
        if (!input.trim() || isLoading) return;

        const userMsg: ChatMessage = {
            id: Date.now().toString(),
            role: 'user',
            content: input.trim(),
        };

        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setIsLoading(true);

        try {
            // Pass the server session ID (null on the very first message).
            // The server will create a new session and return its UUID if null.
            const response = await sendChatMessage(userMsg.content, sessionId);

            // Persist the server-assigned session ID for subsequent messages
            if (response.session_id && response.session_id !== sessionId) {
                localStorage.setItem(SESSION_STORAGE_KEY, response.session_id);
                setSessionId(response.session_id);
            }

            const assistantMsg: ChatMessage = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: response.answer,
            };
            setMessages(prev => [...prev, assistantMsg]);

        } catch (error: any) {
            const errorMsg: ChatMessage = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `**Error connecting to RAG Agent:** ${error.message}`,
            };
            setMessages(prev => [...prev, errorMsg]);
        } finally {
            setIsLoading(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    return (
        <div className="chat-view" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

            {/* Header bar — shows active session indicator + New Conversation button */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 20px',
                borderBottom: '1px solid var(--border-color)',
                background: 'var(--bg-primary)',
                flexShrink: 0,
            }}>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    {sessionId
                        ? `Session: ${sessionId.slice(0, 8)}…`
                        : 'No active session'}
                </span>
                <button
                    onClick={handleNewConversation}
                    title="Clear history and start a new conversation"
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: '8px',
                        padding: '6px 12px',
                        color: 'var(--text-secondary)',
                        fontSize: '0.8rem',
                        cursor: 'pointer',
                    }}
                >
                    <RotateCcw size={13} />
                    New Conversation
                </button>
            </div>

            {/* Messages Area */}
            <div className="messages-container" style={{ flex: 1, overflowY: 'auto', padding: '40px 20px' }}>
                <div style={{ maxWidth: '800px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '24px' }}>

                    {messages.map((msg) => {
                        const isUser = msg.role === 'user';
                        return (
                            <div
                                key={msg.id}
                                style={{
                                    display: 'flex',
                                    width: '100%',
                                    justifyContent: isUser ? 'flex-end' : 'flex-start',
                                }}
                            >
                                <div style={{
                                    display: 'flex',
                                    gap: '12px',
                                    flexDirection: isUser ? 'row-reverse' : 'row',
                                    maxWidth: '85%',
                                }}>
                                    <div style={{ flexShrink: 0, marginTop: '4px' }}>
                                        {isUser ? (
                                            <div style={{ background: 'var(--accent-blue)', width: '30px', height: '30px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>
                                                <User size={16} />
                                            </div>
                                        ) : (
                                            <div style={{ background: 'var(--bg-tertiary)', width: '30px', height: '30px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
                                                <Bot size={16} />
                                            </div>
                                        )}
                                    </div>
                                    <div style={{
                                        background: isUser ? 'var(--accent-blue)' : 'var(--bg-secondary)',
                                        color: isUser ? 'white' : 'var(--text-primary)',
                                        padding: '16px 20px',
                                        borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                                        border: isUser ? 'none' : '1px solid var(--border-color)',
                                        boxShadow: '0 2px 5px rgba(0,0,0,0.05)',
                                        overflowX: 'auto',
                                        lineHeight: '1.6'
                                    }}>
                                        <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                p: ({ node, ...props }) => <p style={{ margin: '0 0 1em 0', color: 'inherit' }} {...props} />,
                                                a: ({ node, ...props }) => <a style={{ color: isUser ? 'white' : 'var(--accent-blue)', textDecoration: 'underline' }} {...props} />,
                                                table: ({ node, ...props }) => <table style={{ borderCollapse: 'collapse', width: '100%', marginBottom: '1em', fontSize: '0.9rem', color: isUser ? 'white' : 'var(--text-primary)' }} {...props} />,
                                                th: ({ node, ...props }) => <th style={{ border: '1px solid', borderColor: isUser ? 'rgba(255,255,255,0.2)' : 'var(--border-highlight)', padding: '10px', background: isUser ? 'rgba(0,0,0,0.1)' : 'var(--bg-primary)', textAlign: 'left', fontWeight: '600' }} {...props} />,
                                                td: ({ node, ...props }) => <td style={{ border: '1px solid', borderColor: isUser ? 'rgba(255,255,255,0.2)' : 'var(--border-highlight)', padding: '10px', background: isUser ? 'transparent' : 'var(--bg-secondary)' }} {...props} />,
                                                strong: ({ node, ...props }) => <strong style={{ fontWeight: '600', color: 'inherit' }} {...props} />
                                            }}
                                        >
                                            {msg.content}
                                        </ReactMarkdown>
                                    </div>
                                </div>
                            </div>
                        );
                    })}

                    {isLoading && (
                        <div style={{ display: 'flex', width: '100%', justifyContent: 'flex-start' }}>
                            <div style={{ display: 'flex', gap: '12px', flexDirection: 'row', maxWidth: '85%' }}>
                                <div style={{ flexShrink: 0, marginTop: '4px' }}>
                                    <div style={{ background: 'var(--bg-tertiary)', width: '30px', height: '30px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--border-color)', color: 'var(--text-primary)' }}>
                                        <Bot size={16} />
                                    </div>
                                </div>
                                <div style={{
                                    background: 'var(--bg-secondary)',
                                    color: 'var(--text-secondary)',
                                    padding: '16px 20px',
                                    borderRadius: '16px 16px 16px 4px',
                                    border: '1px solid var(--border-color)',
                                    display: 'flex',
                                    alignItems: 'center'
                                }}>
                                    <Loader2 size={16} className="spin" style={{ animation: 'spin 1s linear infinite', marginRight: '8px' }} />
                                    Thinking...
                                </div>
                            </div>
                        </div>
                    )}

                    <div ref={messagesEndRef} />
                </div>
            </div>

            {/* Input Area */}
            <div className="input-container" style={{ padding: '20px', background: 'var(--bg-primary)', borderTop: '1px solid var(--border-color)' }}>
                <div style={{ maxWidth: '800px', margin: '0 auto', position: 'relative' }}>
                    <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask about portfolio health, project status, risks, or financial variance..."
                        style={{
                            width: '100%',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-highlight)',
                            color: 'var(--text-primary)',
                            borderRadius: '12px',
                            padding: '16px 50px 16px 16px',
                            fontSize: '1rem',
                            resize: 'none',
                            minHeight: '60px',
                            maxHeight: '200px',
                            fontFamily: 'inherit',
                            lineHeight: '1.5',
                            outline: 'none',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
                        }}
                        rows={1}
                    />
                    <button
                        onClick={handleSend}
                        disabled={!input.trim() || isLoading}
                        style={{
                            position: 'absolute',
                            right: '12px',
                            bottom: '12px',
                            background: input.trim() ? 'var(--accent-blue)' : 'var(--bg-tertiary)',
                            color: input.trim() ? 'white' : 'var(--text-secondary)',
                            border: 'none',
                            borderRadius: '8px',
                            width: '36px',
                            height: '36px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            cursor: input.trim() ? 'pointer' : 'default',
                            transition: 'background 0.2s',
                        }}
                    >
                        <Send size={18} />
                    </button>
                </div>
                <div style={{ textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.75rem', marginTop: '12px' }}>
                    Sentra RAG Agent may produce inaccurate information about projects or financials. Verify via MPPR Excel if necessary.
                </div>
            </div>
        </div>
    );
};

export default ChatView;
