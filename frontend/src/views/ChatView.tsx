import React, { useState, useRef, useEffect } from 'react';
import { Send, User, Bot, Loader2 } from 'lucide-react';

interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
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

    const handleSend = async () => {
        if (!input.trim() || isLoading) return;

        const userMsg: ChatMessage = {
            id: Date.now().toString(),
            role: 'user',
            content: input.trim()
        };

        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setIsLoading(true);

        // Simulate an API call to the future Python /api/chat endpoint
        setTimeout(() => {
            const assistantMsg: ChatMessage = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `This is a simulated response. In Phase 4, the Python backend will use the RAG system to answer: "${userMsg.content}"\n\nFor example, if you asked about portfolio health, I would query the Vector DB for P08 and return a summary of Red/Amber/Green projects.`
            };
            setMessages(prev => [...prev, assistantMsg]);
            setIsLoading(false);
        }, 1500);
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    return (
        <div className="chat-view" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

            {/* Messages Area */}
            <div className="messages-container" style={{ flex: 1, overflowY: 'auto', padding: '40px 20px' }}>
                <div style={{ maxWidth: '800px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '24px' }}>

                    {messages.map((msg) => (
                        <div
                            key={msg.id}
                            style={{
                                display: 'flex',
                                gap: '16px',
                                background: msg.role === 'assistant' ? 'var(--bg-secondary)' : 'transparent',
                                padding: '24px',
                                borderRadius: '8px',
                                border: msg.role === 'assistant' ? '1px solid var(--border-color)' : 'none'
                            }}
                        >
                            <div style={{ flexShrink: 0 }}>
                                {msg.role === 'assistant' ? (
                                    <div style={{ background: 'var(--accent-blue)', width: '30px', height: '30px', borderRadius: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>
                                        <Bot size={18} />
                                    </div>
                                ) : (
                                    <div style={{ background: 'var(--bg-tertiary)', width: '30px', height: '30px', borderRadius: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--border-color)' }}>
                                        <User size={18} />
                                    </div>
                                )}
                            </div>
                            <div style={{ flex: 1, lineHeight: '1.6', color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                                {msg.content}
                            </div>
                        </div>
                    ))}

                    {isLoading && (
                        <div style={{ display: 'flex', gap: '16px', padding: '24px' }}>
                            <div style={{ background: 'var(--accent-blue)', width: '30px', height: '30px', borderRadius: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>
                                <Bot size={18} />
                            </div>
                            <div style={{ flex: 1, display: 'flex', alignItems: 'center', color: 'var(--text-secondary)' }}>
                                <Loader2 size={16} className="spin" style={{ animation: 'spin 1s linear infinite', marginRight: '8px' }} />
                                Thinking...
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
