import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, XCircle, CheckCircle2, Loader2, UploadCloud, ChevronDown, ChevronRight, Info, X } from 'lucide-react';
import { batchValidate } from '../services/api';

const BatchValidateView = () => {
    const [uploadedFile, setUploadedFile] = useState<File | null>(null);
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState<any[]>([]);
    const [total, setTotal] = useState(0);
    const [period, setPeriod] = useState('');
    const [error, setError] = useState('');
    const [expandedRow, setExpandedRow] = useState<string | null>(null);

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || e.target.files.length === 0) return;
        setUploadedFile(e.target.files[0]);
    };

    const handleBatchValidate = async () => {
        if (!uploadedFile) {
            setError('Please upload an MPPR Excel file.');
            return;
        }

        setLoading(true);
        setError('');
        setResults([]);
        setExpandedRow(null);

        try {
            const data = await batchValidate(uploadedFile);
            setResults(data.results || []);
            setTotal(data.total || 0);
            setPeriod(data.period || '');
        } catch (err: any) {
            setError(err.message || 'An error occurred during batch validation');
        } finally {
            setLoading(false);
        }
    };

    const getVerdictColor = (verdict: string) => {
        if (verdict === 'PASS') return 'var(--status-pass)';
        if (verdict === 'FAIL') return 'var(--status-fail)';
        if (verdict === 'ERROR') return 'var(--status-fail)';
        if (verdict === 'SKIPPED') return 'var(--text-secondary)';
        return 'var(--status-warn)';
    };

    const getVerdictIcon = (verdict: string) => {
        if (verdict === 'PASS') return <CheckCircle2 size={18} />;
        if (verdict === 'FAIL') return <XCircle size={18} />;
        if (verdict === 'ERROR') return <XCircle size={18} />;
        if (verdict === 'SKIPPED') return <Info size={18} />;
        return <AlertTriangle size={18} />;
    };

    const getVerdictBg = (verdict: string) => {
        if (verdict === 'PASS') return 'var(--status-pass-bg)';
        if (verdict === 'FAIL') return 'var(--status-fail-bg)';
        if (verdict === 'ERROR') return 'var(--status-fail-bg)';
        if (verdict === 'SKIPPED') return 'var(--bg-secondary)';
        return 'var(--status-warn-bg)';
    };

    const toggleRow = (projectName: string) => {
        if (expandedRow === projectName) {
            setExpandedRow(null);
        } else {
            setExpandedRow(projectName);
        }
    };

    return (
        <div className="validate-view">
            <div className="page-header">
                <h1 className="page-title">Batch Narrative Validation</h1>
                <p className="page-subtitle">Process and validate an entire MPPR Excel sheet at once.</p>
            </div>

            {/* Error Popup */}
            {error && (
                <div className="modal-overlay" onClick={() => setError('')}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                            <div style={{ color: 'var(--status-fail)', display: 'flex', gap: '8px', alignItems: 'center', fontWeight: 'bold', fontSize: '1.1rem' }}>
                                <AlertTriangle size={24} />
                                Batch Validation Error
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

            <div className="layout-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(350px, 1fr) 2fr', gap: '32px', height: 'calc(100vh - 180px)' }}>

                {/* Left Column: Input Form */}
                <div className="input-section" style={{ height: '100%', overflowY: 'auto', paddingRight: '8px' }}>
                    <div className="card">
                        <h2 className="card-title">Run Batch Validation</h2>

                        <div className="form-group">
                            <label className="form-label">MPPR Data Source (Excel)</label>
                            <div style={{
                                border: '2px dashed var(--border-color)',
                                borderRadius: '8px',
                                padding: '24px',
                                textAlign: 'center',
                                background: 'var(--bg-secondary)',
                                cursor: 'pointer',
                                position: 'relative'
                            }}>
                                <input
                                    type="file"
                                    accept=".xlsx,.xls"
                                    onChange={handleFileUpload}
                                    style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', zIndex: 10 }}
                                    title="Upload Excel File"
                                />
                                {uploadedFile ? (
                                    <div style={{ color: 'var(--accent-blue)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', fontWeight: 'bold' }}>
                                        <ShieldCheck size={32} />
                                        <span>{uploadedFile.name}</span>
                                    </div>
                                ) : (
                                    <div style={{ color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
                                        <UploadCloud size={32} />
                                        <span>Click or drag to upload Excel file</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        <button
                            onClick={handleBatchValidate}
                            className="btn btn-primary"
                            style={{ width: '100%', padding: '14px' }}
                            disabled={loading || !uploadedFile}
                        >
                            {loading ? (
                                <>
                                    <Loader2 size={18} className="spin" style={{ animation: 'spin 1s linear infinite' }} />
                                    Processing Batch...
                                </>
                            ) : (
                                <>
                                    <ShieldCheck size={18} />
                                    Run Batch Validation
                                </>
                            )}
                        </button>
                    </div>
                </div>

                {/* Right Column: Results Table */}
                <div className="results-section" style={{ height: '100%', overflowY: 'auto', paddingRight: '8px' }}>
                    {!results.length && !loading && (
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '400px', opacity: 0.5, padding: '60px 20px' }}>
                            <ShieldCheck size={48} style={{ marginBottom: '16px' }} />
                            <p>Upload a file and run validation to see batch results.</p>
                        </div>
                    )}

                    {loading && (
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '400px', padding: '60px 20px' }}>
                            <Loader2 size={48} className="spin" style={{ marginBottom: '16px', color: 'var(--accent-blue)' }} />
                            <h3 style={{ marginBottom: '8px' }}>Processing Batch Validation...</h3>
                            <p style={{ color: 'var(--text-secondary)' }}>This validates every narrative in the uploaded file.</p>
                        </div>
                    )}

                    {results.length > 0 && !loading && (
                        <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: '8px', borderBottom: '1px solid var(--border-color)' }}>
                                <div>
                                    <h2 style={{ fontSize: '1.2rem', marginBottom: '4px' }}>Batch Results</h2>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                                        Period: <strong>{period}</strong> | Total Processed: <strong>{total}</strong>
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '12px' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem' }}>
                                        <span style={{ display: 'inline-block', width: '10px', height: '10px', borderRadius: '50%', background: 'var(--status-pass)' }}></span> Pass
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem' }}>
                                        <span style={{ display: 'inline-block', width: '10px', height: '10px', borderRadius: '50%', background: 'var(--status-warn)' }}></span> Warn
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem' }}>
                                        <span style={{ display: 'inline-block', width: '10px', height: '10px', borderRadius: '50%', background: 'var(--status-fail)' }}></span> Fail
                                    </div>
                                </div>
                            </div>

                            <div className="table-responsive">
                                <table className="data-table" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '8px' }}>
                                    <thead>
                                        <tr style={{ background: 'var(--bg-secondary)', textAlign: 'left', borderBottom: '2px solid var(--border-color)' }}>
                                            <th style={{ padding: '12px', width: '40px' }}></th>
                                            <th style={{ padding: '12px' }}>Project Name</th>
                                            <th style={{ padding: '12px', width: '120px' }}>Verdict</th>
                                            <th style={{ padding: '12px', width: '100px', textAlign: 'center' }}>Score</th>
                                            <th style={{ padding: '12px', width: '100px', textAlign: 'center' }}>Issues</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {results.map((res: any, idx: number) => {
                                            const vColor = getVerdictColor(res.overall_verdict);
                                            const isExpanded = expandedRow === res.project_name;
                                            const totalIssues = (res.layer1?.issues?.length || 0) + (res.layer2?.issues?.length || 0);

                                            return (
                                                <React.Fragment key={idx}>
                                                    <tr
                                                        onClick={() => toggleRow(res.project_name)}
                                                        style={{
                                                            borderBottom: '1px solid var(--border-color)',
                                                            cursor: 'pointer',
                                                            background: isExpanded ? 'var(--bg-secondary)' : 'var(--bg-primary)'
                                                        }}
                                                        className="hover-row"
                                                    >
                                                        <td style={{ padding: '12px', color: 'var(--text-secondary)' }}>
                                                            {isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                                                        </td>
                                                        <td style={{ padding: '12px', fontWeight: '500' }}>
                                                            {res.project_name}
                                                        </td>
                                                        <td style={{ padding: '12px' }}>
                                                            <div style={{
                                                                display: 'inline-flex',
                                                                alignItems: 'center',
                                                                gap: '6px',
                                                                padding: '4px 8px',
                                                                borderRadius: '4px',
                                                                background: getVerdictBg(res.overall_verdict),
                                                                color: vColor,
                                                                fontWeight: 'bold',
                                                                fontSize: '0.85rem'
                                                            }}>
                                                                {getVerdictIcon(res.overall_verdict)}
                                                                {res.overall_verdict}
                                                            </div>
                                                        </td>
                                                        <td style={{ padding: '12px', textAlign: 'center', fontWeight: 'bold' }}>
                                                            {res.layer1?.compliance_score || '-'}
                                                        </td>
                                                        <td style={{ padding: '12px', textAlign: 'center', color: totalIssues > 0 ? 'var(--status-fail)' : 'var(--text-secondary)' }}>
                                                            {totalIssues > 0 ? totalIssues : '-'}
                                                        </td>
                                                    </tr>

                                                    {/* Expanded Row Detail */}
                                                    {isExpanded && (
                                                        <tr style={{ background: 'var(--bg-primary)' }}>
                                                            <td colSpan={5} style={{ padding: '24px', borderBottom: '2px solid var(--border-color)' }}>
                                                                {res.overall_verdict === 'ERROR' || res.overall_verdict === 'SKIPPED' ? (
                                                                    <div style={{ color: vColor }}>{res.message}</div>
                                                                ) : (
                                                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

                                                                        {/* Layer 1 */}
                                                                        {res.layer1?.issues?.length > 0 && (
                                                                            <div style={{ padding: '16px', borderLeft: '3px solid var(--status-fail)', background: 'var(--bg-secondary)', borderRadius: '0 8px 8px 0' }}>
                                                                                <h4 style={{ color: 'var(--status-fail)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                                    <AlertTriangle size={16} /> Format & Compliance Issues
                                                                                </h4>
                                                                                <ul style={{ margin: 0, paddingLeft: '20px', color: 'var(--text-secondary)' }}>
                                                                                    {res.layer1.issues.map((issue: string, i: number) => (
                                                                                        <li key={i} style={{ marginBottom: '4px' }}>{issue}</li>
                                                                                    ))}
                                                                                </ul>
                                                                            </div>
                                                                        )}

                                                                        {/* Layer 2 */}
                                                                        {res.layer2?.issues?.length > 0 && (
                                                                            <div style={{ padding: '16px', borderLeft: '3px solid var(--status-warn)', background: 'var(--bg-secondary)', borderRadius: '0 8px 8px 0' }}>
                                                                                <h4 style={{ color: 'var(--status-warn)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                                    <Info size={16} /> Data Inconsistencies
                                                                                </h4>
                                                                                <ul style={{ margin: 0, paddingLeft: '20px', color: 'var(--text-secondary)' }}>
                                                                                    {res.layer2.issues.map((issue: string, i: number) => (
                                                                                        <li key={i} style={{ marginBottom: '4px' }}>{issue}</li>
                                                                                    ))}
                                                                                </ul>
                                                                            </div>
                                                                        )}

                                                                        {(res.layer1?.issues?.length === 0 && res.layer2?.issues?.length === 0) && (
                                                                            <div style={{ color: 'var(--status-pass)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                                <CheckCircle2 size={16} /> No issues found for this narrative.
                                                                            </div>
                                                                        )}

                                                                        {/* AI Rewrite Detail */}
                                                                        {res.rewritten_narrative && (
                                                                            <div style={{ padding: '16px', background: 'var(--accent-blue-glow)', border: '1px solid var(--accent-blue)', borderRadius: '8px' }}>
                                                                                <h4 style={{ color: 'var(--accent-blue)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                                                    ✨ AI Rewritten Narrative
                                                                                </h4>
                                                                                <div style={{ background: 'var(--bg-primary)', padding: '12px', borderRadius: '4px', fontSize: '0.9rem', lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
                                                                                    {res.rewritten_narrative}
                                                                                </div>
                                                                            </div>
                                                                        )}

                                                                    </div>
                                                                )}
                                                            </td>
                                                        </tr>
                                                    )}
                                                </React.Fragment>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>

                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default BatchValidateView;
