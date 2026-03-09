import React, { useState, useRef } from 'react';
import { ShieldCheck, AlertTriangle, XCircle, CheckCircle2, Info, Loader2, FileEdit, X, UploadCloud } from 'lucide-react';
import { validateNarrative, listProjects, type ProjectInfo } from '../services/api';

const ValidateView = () => {
    const [projectName, setProjectName] = useState('Sellafield');
    const [period, setPeriod] = useState('P08');
    const [narrative, setNarrative] = useState('');

    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');

    const [uploadedFile, setUploadedFile] = useState<File | null>(null);
    const [projectsList, setProjectsList] = useState<ProjectInfo[]>([]);
    const [isManualEntry, setIsManualEntry] = useState(true);
    const [uploadLoading, setUploadLoading] = useState(false);
    const [toastMessage, setToastMessage] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || e.target.files.length === 0) return;
        const file = e.target.files[0];
        setUploadedFile(file);

        setUploadLoading(true);
        setError('');

        try {
            const data = await listProjects(file);
            setProjectsList(data.projects);
            setPeriod(data.period);
            if (data.projects && data.projects.length > 0) {
                setIsManualEntry(false);
                setProjectName(data.projects[0].project_name);
                setNarrative(data.projects[0].narrative_text || '');
            }
        } catch (err: any) {
            setError(err.message || 'Failed to list projects from file');
            setUploadedFile(null);
        } finally {
            setUploadLoading(false);
            e.target.value = ''; // Reset input
        }
    };

    const handleProjectSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const val = e.target.value;
        if (val === '__MANUAL__') {
            setIsManualEntry(true);
            setProjectName('');
            setNarrative('');
        } else {
            setIsManualEntry(false);
            setProjectName(val);
            const proj = projectsList.find(p => p.project_name === val);
            if (proj) {
                setNarrative(proj.narrative_text || '');
            }
        }
    };

    const handleValidate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!narrative.trim()) {
            setError('Please provide a narrative to validate.');
            return;
        }

        setLoading(true);
        setError('');

        try {
            const response = await validateNarrative({
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
        <div className="validate-view page-container">
            <div className="page-header">
                <h1 className="page-title">Individual Narrative Validation</h1>
                <p className="page-subtitle">Verify project narratives against core guidelines and EAC variance data.</p>
            </div>

            {/* Error Popup Modal */}
            {error && (
                <div className="modal-overlay" onClick={() => setError('')}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <div style={{ color: 'var(--status-fail)', display: 'flex', gap: '8px', alignItems: 'center', fontWeight: 'bold', fontSize: '1.1rem' }}>
                                <AlertTriangle size={24} />
                                Validation Error
                            </div>
                            <button onClick={() => setError('')} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                                <X size={20} />
                            </button>
                        </div>
                        <p style={{ color: 'var(--text-primary)', lineHeight: '1.5' }}>{error}</p>
                        <div style={{ marginTop: '24px', display: 'flex', justifyContent: 'flex-end' }}>
                            <button onClick={() => setError('')} className="btn btn-primary">
                                Dismiss
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <div className="layout-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(400px, 1fr) 1.5fr', gap: '32px' }}>

                {/* Left Column: Input Form */}
                <div className="input-section">
                    <div className="card">
                        <h2 className="card-title">Project Context</h2>
                        <form onSubmit={handleValidate}>
                            <div className="form-group">
                                <label className="form-label">Source Data (Optional)</label>
                                <div style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '12px',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '8px',
                                    padding: '8px 12px',
                                    background: 'var(--bg-secondary)',
                                    position: 'relative'
                                }}>
                                    <input
                                        type="file"
                                        accept=".xlsx,.xls"
                                        onChange={handleFileUpload}
                                        style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', zIndex: 10 }}
                                        title="Upload Excel File"
                                    />
                                    <UploadCloud size={20} style={{ color: 'var(--text-secondary)' }} />
                                    <div style={{ flex: 1 }}>
                                        {uploadLoading ? (
                                            <span style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.9rem' }}>
                                                <Loader2 size={16} className="spin" /> Processing...
                                            </span>
                                        ) : uploadedFile ? (
                                            <span style={{ color: 'var(--accent-blue)', fontWeight: 'bold', fontSize: '0.9rem' }}>
                                                {uploadedFile.name} ({projectsList.length} loaded)
                                            </span>
                                        ) : (
                                            <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                                                Click to upload MPPR Excel (auto-fill)
                                            </span>
                                        )}
                                    </div>
                                    {uploadedFile && (
                                        <ShieldCheck size={20} style={{ color: 'var(--status-pass)' }} />
                                    )}
                                </div>
                            </div>

                            <div style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
                                <div style={{ flex: 1 }}>
                                    <label className="form-label">Project Name</label>
                                    {projectsList.length > 0 && !isManualEntry ? (
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <select
                                                className="form-control"
                                                value={projectName}
                                                onChange={handleProjectSelect}
                                                style={{ flex: 1, backgroundColor: 'var(--bg-primary)' }}
                                            >
                                                {projectsList.map((p, idx) => (
                                                    <option key={idx} value={p.project_name}>{p.project_name}</option>
                                                ))}
                                                <option value="__MANUAL__">Type manually...</option>
                                            </select>
                                        </div>
                                    ) : (
                                        <div style={{ display: 'flex', gap: '8px' }}>
                                            <input
                                                type="text"
                                                className="form-control"
                                                value={projectName}
                                                onChange={(e) => setProjectName(e.target.value)}
                                            />
                                            {projectsList.length > 0 && (
                                                <button type="button" onClick={() => {
                                                    setIsManualEntry(false);
                                                    setProjectName(projectsList[0].project_name);
                                                    setNarrative(projectsList[0].narrative_text || '');
                                                }} className="btn btn-outline" style={{ padding: '0 12px', whiteSpace: 'nowrap' }}>
                                                    Back to List
                                                </button>
                                            )}
                                        </div>
                                    )}
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
                                    ref={textareaRef}
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
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '400px', opacity: 0.5, padding: '60px 20px' }}>
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
                                            type="button"
                                            onClick={() => {
                                                setNarrative(result.rewritten_narrative);
                                                setToastMessage('Narrative updated! Ready for re-validation.');
                                                setTimeout(() => setToastMessage(''), 3000);
                                                textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                            }}
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

            {/* Toast Notification */}
            {toastMessage && (
                <div style={{
                    position: 'fixed',
                    bottom: '32px',
                    right: '32px',
                    background: 'var(--status-pass-bg)',
                    color: 'var(--status-pass)',
                    padding: '16px 24px',
                    borderRadius: '8px',
                    border: '1px solid var(--status-pass)',
                    boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    zIndex: 1000,
                    animation: 'fadeIn 0.3s ease-out'
                }}>
                    <CheckCircle2 size={24} />
                    <span style={{ fontWeight: '600' }}>{toastMessage}</span>
                </div>
            )}
        </div>
    );
};

export default ValidateView;
