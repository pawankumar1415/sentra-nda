import React, { useState, useRef, useEffect } from 'react';
import { Send, User, Bot, Loader2, Key } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { sendChatMessage } from '../services/api';
import type { ChatMessage as ApiChatMessage } from '../services/api';

interface ChatMessage extends ApiChatMessage {
    id: string;
}

const ChatView = () => {
    const [messages, setMessages] = useState<ChatMessage[]>([
        {
            id: '1',
            role: 'assistant',
            content: "Hello! I am the Sentra Portfolio RAG Assistant. I have access to the latest NDA MPPR and EAC reference data. How can I help you analyze the portfolio today?"
        }
    ]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isLoading]);

    const [apiKey, setApiKey] = useState('');

    const handleSend = async () => {
        if (!input.trim() || isLoading) return;
        if (!apiKey) {
            alert("Please enter your Azure Function API Key at the top first.");
            return;
        }

        const userMsg: ChatMessage = {
            id: Date.now().toString(),
            role: 'user',
            content: input.trim()
        };

        const newMessages = [...messages, userMsg];
        setMessages(newMessages);
        setInput('');
        setIsLoading(true);

        try {
            // Send history excluding the new user message (we send it separate, or send all)
            // The API expects: { question: string, history: ChatMessage[] }
            // Let's send the previous history up to the user message
            const historyToSent = messages.map(m => ({ role: m.role, content: m.content }));

            const response = await sendChatMessage(apiKey, userMsg.content, historyToSent);

            const assistantMsg: ChatMessage = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: response.answer
            };
            setMessages(prev => [...prev, assistantMsg]);

        } catch (error: any) {
            const errorMsg: ChatMessage = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `**Error connecting to RAG Agent:** ${error.message}`
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

            {/* Top API Key Bar */}
            <div style={{ background: 'var(--bg-primary)', padding: '12px 20px', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '12px' }}>
                <Key size={16} style={{ color: 'var(--text-secondary)' }} />
                <input
                    type="password"
                    placeholder="Enter Global Azure Function Key..."
                    style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', width: '300px', fontSize: '0.9rem' }}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                />
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
