import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, XCircle, CheckCircle2, Info, Loader2, FileEdit } from 'lucide-react';
import { validateNarrative } from '../services/api';

const ValidateView = () => {
    const [apiKey, setApiKey] = useState('');
    const [projectName, setProjectName] = useState('Sellafield');
    const [period, setPeriod] = useState('P08');
    const [narrative, setNarrative] = useState('');

    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');

    const handleValidate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!apiKey) {
            setError('Please provide your Azure Function Key.');
            return;
        }
        if (!narrative.trim()) {
            setError('Please provide a narrative to validate.');
            return;
        }

        setLoading(true);
        setError('');

        try {
            const response = await validateNarrative(apiKey, {
                narrative,
                project_name: projectName,
                period
            });
            setResult(response);
        } catch (err: any) {
            setError(err.message || 'An error occurred during validation');
        } finally {
            setLoading(false);
        }
    };

    const getVerdictColor = (verdict: string) => {
        if (verdict === 'PASS') return 'var(--status-pass)';
        if (verdict === 'FAIL') return 'var(--status-fail)';
        return 'var(--status-warn)';
    };

    const getVerdictBg = (verdict: string) => {
        if (verdict === 'PASS') return 'var(--status-pass-bg)';
        if (verdict === 'FAIL') return 'var(--status-fail-bg)';
        return 'var(--status-warn-bg)';
    };

    return (
        <div className="validate-view">
            <div className="page-header">
                <h1 className="page-title">Narrative Validation</h1>
                <p className="page-subtitle">Verify project narratives against core guidelines and EAC variance data.</p>
            </div>

            {error && (
                <div className="card animate-fade-in" style={{ borderColor: 'var(--status-fail)', borderLeftWidth: '4px' }}>
                    <div style={{ color: 'var(--status-fail)', display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <AlertTriangle size={20} />
                        <strong>Error:</strong> {error}
                    </div>
                </div>
            )}

            <div className="layout-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '32px' }}>

                {/* Left Column: Input Form */}
                <div className="input-section">
                    <div className="card">
                        <h2 className="card-title">Project Context</h2>
                        <form onSubmit={handleValidate}>
                            <div className="form-group">
                                <label className="form-label">Azure Function Key</label>
                                <input
                                    type="password"
                                    className="form-control"
                                    value={apiKey}
                                    onChange={(e) => setApiKey(e.target.value)}
                                    placeholder="Paste your function key here..."
                                />
                            </div>

                            <div style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
                                <div style={{ flex: 1 }}>
                                    <label className="form-label">Project Name</label>
                                    <input
                                        type="text"
                                        className="form-control"
                                        value={projectName}
                                        onChange={(e) => setProjectName(e.target.value)}
                                    />
                                </div>
                                <div style={{ width: '120px' }}>
                                    <label className="form-label">Period</label>
                                    <input
                                        type="text"
                                        className="form-control"
                                        value={period}
                                        onChange={(e) => setPeriod(e.target.value)}
                                    />
                                </div>
                            </div>

                            <div className="form-group">
                                <label className="form-label">Draft Narrative</label>
                                <textarea
                                    className="form-control"
                                    rows={8}
                                    value={narrative}
                                    onChange={(e) => setNarrative(e.target.value)}
                                    placeholder="Paste the executive summary narrative here..."
                                ></textarea>
                            </div>

                            <button
                                type="submit"
                                className="btn btn-primary"
                                style={{ width: '100%', padding: '14px' }}
                                disabled={loading}
                            >
                                {loading ? (
                                    <>
                                        <Loader2 size={18} className="spin" style={{ animation: 'spin 1s linear infinite' }} />
                                        Analyzing Narrative...
                                    </>
                                ) : (
                                    <>
                                        <ShieldCheck size={18} />
                                        Validate & Verify
                                    </>
                                )}
                            </button>
                        </form>
                    </div>
                </div>

                {/* Right Column: Results */}
                <div className="results-section">
                    {!result && !loading && (
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', opacity: 0.5, minHeight: '400px' }}>
                            <ShieldCheck size={48} style={{ marginBottom: '16px' }} />
                            <p>Submit a narrative to see compliance results.</p>
                        </div>
                    )}

                    {result && (
                        <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>

                            {/* Verdict Header */}
                            <div className="card" style={{
                                background: getVerdictBg(result.overall_verdict),
                                borderColor: getVerdictColor(result.overall_verdict),
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between'
                            }}>
                                <div>
                                    <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '1px' }}>Overall Verdict</h3>
                                    <div style={{ fontSize: '1.8rem', fontWeight: '700', color: getVerdictColor(result.overall_verdict), display: 'flex', gap: '12px', alignItems: 'center' }}>
                                        {result.overall_verdict === 'PASS' ? <CheckCircle2 size={32} /> : result.overall_verdict === 'FAIL' ? <XCircle size={32} /> : <AlertTriangle size={32} />}
                                        {result.overall_verdict.replace('_', ' ')}
                                    </div>
                                </div>
                                <div style={{ textAlign: 'right' }}>
                                    <div style={{ fontSize: '2rem', fontWeight: '800' }}>{result.layer1.compliance_score}/10</div>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>Compliance Score</div>
                                </div>
                            </div>

                            {/* Layer 1: Structural Issues */}
                            {result.layer1.issues.length > 0 && (
                                <div className="card">
                                    <h2 className="card-title" style={{ color: 'var(--status-fail)' }}>
                                        <AlertTriangle size={18} /> Format & Compliance Issues ({result.layer1.issues.length})
                                    </h2>
                                    <ul style={{ listStylePosition: 'inside', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                        {result.layer1.issues.map((issue: string, idx: number) => (
                                            <li key={idx} style={{ lineHeight: '1.4' }}>{issue}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {/* Layer 2: Data Variances */}
                            {result.layer2.issues.length > 0 && (
                                <div className="card">
                                    <h2 className="card-title" style={{ color: 'var(--status-warn)' }}>
                                        <Info size={18} /> Data Inconsistencies ({result.layer2.issues.length})
                                    </h2>
                                    <ul style={{ listStylePosition: 'inside', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                        {result.layer2.issues.map((issue: string, idx: number) => (
                                            <li key={idx} style={{ lineHeight: '1.4' }}>{issue}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {result.layer1.issues.length === 0 && result.layer2.issues.length === 0 && (
                                <div className="card" style={{ borderColor: 'var(--status-pass)' }}>
                                    <h2 className="card-title" style={{ color: 'var(--status-pass)' }}>
                                        <CheckCircle2 size={18} /> Perfect Alignment
                                    </h2>
                                    <p style={{ color: 'var(--text-secondary)' }}>The narrative perfectly matches all formatting guidelines and baseline data.</p>
                                </div>
                            )}

                            {/* AI Rewrite */}
                            {result.rewritten_narrative && (
                                <div className="card glass-panel" style={{ background: 'var(--accent-blue-glow)', borderColor: 'var(--accent-blue)' }}>
                                    <h2 className="card-title" style={{ color: 'var(--accent-blue)' }}>
                                        ✨ AI Rewritten Narrative
                                    </h2>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                                        <div style={{ background: 'var(--bg-primary)', padding: '16px', borderRadius: '8px', lineHeight: '1.6', color: 'var(--text-primary)', borderLeft: '3px solid var(--accent-blue)', boxShadow: '0 1px 2px rgba(0,0,0,0.05)', fontSize: '0.95rem' }}>
                                            {result.rewritten_narrative}
                                        </div>
                                        <button
                                            onClick={() => setNarrative(result.rewritten_narrative)}
                                            className="btn btn-primary"
                                            style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 16px', fontSize: '0.9rem' }}
                                        >
                                            <FileEdit size={16} />
                                            Use This Rewrite
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Underlying Data Context */}
                            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textAlign: 'right', marginTop: '-8px' }}>
                                EAC Shift Context: £{(result._meta.eac_variance_m || 0).toFixed(1)}m | Schedule Shift: {result._meta.schedule_days} days
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default ValidateView;
