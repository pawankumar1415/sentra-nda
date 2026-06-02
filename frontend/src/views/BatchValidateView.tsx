import React, { useState } from 'react';
import { ShieldCheck, AlertTriangle, XCircle, CheckCircle2, Loader2, UploadCloud, ChevronDown, ChevronRight, Info, X, Download, Share2, RotateCcw } from 'lucide-react';
import { listProjects, validateNarrative, listSharePointFiles, listProjectsFromSharePoint, type SharePointFile } from '../services/api';
import { useValidation, generateId } from '../context/ValidationContext';
import * as XLSX from 'xlsx';

const BatchValidateView = () => {
    // ── Persisted state (survives page navigation) ────────────────────────────
    const { batchState, setBatchState, clearBatchState, addToHistory } = useValidation();
    const { results, total, period, progress } = batchState;

    // ── Local-only state (intentionally reset on navigation) ──────────────────
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [expandedRow, setExpandedRow] = useState<string | null>(null);

    // ── SharePoint source state ────────────────────────────────────────────
    const [source, setSource] = useState<'local' | 'sharepoint'>('local');
    const [spFiles, setSpFiles] = useState<SharePointFile[]>([]);
    const [spFilesLoading, setSpFilesLoading] = useState(false);
    const [spFileId, setSpFileId] = useState('');
    const [spProjects, setSpProjects] = useState<{ project_name: string; narrative_text: string }[] | null>(null);
    const [spProjectsLoading, setSpProjectsLoading] = useState(false);
    const [spPeriod, setSpPeriod] = useState('');
    const [uploadedFile, setUploadedFile] = useState<File | null>(null);

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || e.target.files.length === 0) return;
        const file = e.target.files[0];
        if (!file.name.toLowerCase().endsWith('.xlsx')) {
            setError('Only .xlsx files are accepted. Please upload an Excel spreadsheet.');
            e.target.value = '';
            return;
        }
        setError('');
        setUploadedFile(file);
        setSpProjects(null);
    };

    // ── SharePoint handlers ────────────────────────────────────────────────
    const handleSourceChange = async (newSource: 'local' | 'sharepoint') => {
        setSource(newSource);
        setUploadedFile(null);
        setSpFileId('');
        setSpProjects(null);

        if (newSource === 'sharepoint' && spFiles.length === 0) {
            setSpFilesLoading(true);
            try {
                const files = await listSharePointFiles();
                setSpFiles(files);
            } catch (err: any) {
                setError(err.message || 'Failed to load SharePoint files');
            } finally {
                setSpFilesLoading(false);
            }
        }
    };

    const handleSpFileSelect = async (fileId: string) => {
        setSpFileId(fileId);
        setSpProjects(null);
        if (!fileId) return;

        setSpProjectsLoading(true);
        setError('');
        try {
            const data = await listProjectsFromSharePoint(fileId);
            setSpProjects(data.projects);
            setSpPeriod(data.period);
        } catch (err: any) {
            setError(err.message || 'Failed to load projects from SharePoint file');
        } finally {
            setSpProjectsLoading(false);
        }
    };

    const handleBatchValidate = async () => {
        if (source === 'local' && !uploadedFile) {
            setError('Please upload an MPPR Excel file.');
            return;
        }
        if (source === 'sharepoint' && !spProjects) {
            setError('Please select a SharePoint file first.');
            return;
        }

        setLoading(true);
        setError('');
        setExpandedRow(null);
        setBatchState({
            results:  [],
            total:    0,
            period:   '',
            progress: { current: 0, total: 0, statusText: 'Extracting projects from Excel...' },
        });

        const batchId = generateId();

        try {
            // Step 1: Get projects — from local upload or SharePoint
            let projects: { project_name: string; narrative_text: string }[];
            let resolvedPeriod: string;

            if (source === 'sharepoint' && spProjects) {
                projects = spProjects;
                resolvedPeriod = spPeriod;
            } else {
                const listData = await listProjects(uploadedFile!);
                projects = listData.projects || [];
                resolvedPeriod = listData.period || '';
            }

            if (projects.length === 0) {
                throw new Error('No projects found in the selected file.');
            }

            setBatchState(prev => ({ ...prev, total: projects.length, period: resolvedPeriod }));

            const currentResults: any[] = [];

            // Step 2: Validate each project individually for live progress
            for (let i = 0; i < projects.length; i++) {
                const proj = projects[i];
                setBatchState(prev => ({
                    ...prev,
                    progress: { current: i, total: projects.length, statusText: `Validating ${proj.project_name}...` },
                }));

                try {
                    const validationResult = await validateNarrative({
                        narrative:    proj.narrative_text,
                        project_name: proj.project_name,
                        period:       resolvedPeriod,
                    });
                    currentResults.push({ ...validationResult, project_name: proj.project_name, _narrative: proj.narrative_text });
                } catch (err: any) {
                    currentResults.push({
                        project_name:    proj.project_name,
                        overall_verdict: 'ERROR',
                        message:         err.message || 'Validation failed',
                    });
                }

                setBatchState(prev => ({ ...prev, results: [...currentResults] }));
            }

            setBatchState(prev => ({
                ...prev,
                progress: { current: projects.length, total: projects.length, statusText: 'Validation Complete!' },
            }));

            // Record every project as its own history entry
            addToHistory(
                currentResults.map(res => ({
                    type:                 'batch' as const,
                    project_name:         res.project_name,
                    period:               resolvedPeriod,
                    verdict:              (res.overall_verdict === 'PASS_WITH_WARNINGS' ? 'WARN' : res.overall_verdict || 'ERROR') as 'PASS' | 'WARN' | 'FAIL' | 'ERROR',
                    score:                res.layer1?.compliance_score ?? null,
                    batch_id:             batchId,
                    narrative:            res._narrative ?? undefined,
                    rewritten_narrative:  res.rewritten_narrative ?? undefined,
                }))
            );

        } catch (err: any) {
            setError(err.message || 'An error occurred during batch validation');
        } finally {
            setLoading(false);
        }
    };

    const getVerdictColor = (verdict: string) => {
        if (verdict === 'PASS')    return 'var(--status-pass)';
        if (verdict === 'FAIL')    return 'var(--status-fail)';
        if (verdict === 'ERROR')   return 'var(--status-fail)';
        if (verdict === 'SKIPPED') return 'var(--text-secondary)';
        return 'var(--status-warn)';
    };

    const getVerdictIcon = (verdict: string) => {
        if (verdict === 'PASS')    return <CheckCircle2 size={18} />;
        if (verdict === 'FAIL')    return <XCircle size={18} />;
        if (verdict === 'ERROR')   return <XCircle size={18} />;
        if (verdict === 'SKIPPED') return <Info size={18} />;
        return <AlertTriangle size={18} />;
    };

    const getVerdictBg = (verdict: string) => {
        if (verdict === 'PASS')    return 'var(--status-pass-bg)';
        if (verdict === 'FAIL')    return 'var(--status-fail-bg)';
        if (verdict === 'ERROR')   return 'var(--status-fail-bg)';
        if (verdict === 'SKIPPED') return 'var(--bg-secondary)';
        return 'var(--status-warn-bg)';
    };

    const toggleRow = (projectName: string) => {
        setExpandedRow(prev => (prev === projectName ? null : projectName));
    };

    const handleExport = () => {
        if (!results.length) return;

        const exportData = results.map(res => ({
            'Project Name':             res.project_name || res._meta?.project_name,
            'Period':                   res._meta?.period || period,
            'Overall Verdict':          res.overall_verdict,
            'Compliance Score':         res.layer1?.compliance_score || 0,
            'EAC Variance (£m)':        res._meta?.eac_variance_m || 0,
            'Schedule Variance (Days)': res._meta?.schedule_days || 0,
            'Compliance Issues':        res.layer1?.issues?.join('\n') || 'None',
            'Data Inconsistencies':     res.layer2?.issues?.join('\n') || 'None',
            'Original Validation Input':res._meta?.narrative_excerpt || 'N/A',
            'AI Rewritten Narrative':   res.rewritten_narrative || 'N/A',
        }));

        const ws = XLSX.utils.json_to_sheet(exportData);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Batch Validation Results');
        XLSX.writeFile(wb, `Sentra_Batch_Validation_${period || 'Export'}.xlsx`);
    };

    // Whether a previous run was interrupted mid-way
    const isPartialRun = !loading && progress.total > 0 && progress.current < progress.total;

    return (
        <div className="validate-view page-container">
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
                            <button onClick={() => setError('')} className="btn btn-primary">Dismiss</button>
                        </div>
                    </div>
                </div>
            )}

            <div className="layout-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(350px, 1fr) 2fr', gap: '32px' }}>

                {/* Left Column: Input Form */}
                <div className="input-section" style={{ paddingRight: '8px' }}>
                    <div className="card">
                        <h2 className="card-title">Run Batch Validation</h2>

                        <div className="form-group">
                            <label className="form-label">MPPR Data Source (Excel)</label>

                            {/* Source toggle */}
                            <div style={{ display: 'flex', gap: '8px', marginBottom: '10px' }}>
                                {(['local', 'sharepoint'] as const).map(s => (
                                    <button
                                        key={s}
                                        type="button"
                                        onClick={() => handleSourceChange(s)}
                                        className={source === s ? 'btn btn-primary' : 'btn btn-outline'}
                                        style={{ flex: 1, padding: '7px 12px', fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
                                    >
                                        {s === 'local' ? <UploadCloud size={15} /> : <Share2 size={15} />}
                                        {s === 'local' ? 'Local Upload' : 'SharePoint Library'}
                                    </button>
                                ))}
                            </div>

                            {/* Local upload */}
                            {source === 'local' && (
                                <div style={{
                                    display: 'flex', alignItems: 'center', gap: '12px',
                                    border: '1px dashed var(--accent-blue)', borderRadius: '8px',
                                    padding: '12px 16px', background: 'var(--accent-blue-glow)',
                                    position: 'relative', cursor: 'pointer'
                                }}>
                                    <input
                                        type="file"
                                        accept=".xlsx"
                                        onChange={handleFileUpload}
                                        style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', zIndex: 10 }}
                                        title="Upload Excel File"
                                    />
                                    <UploadCloud size={20} style={{ color: 'var(--accent-blue)' }} />
                                    <div style={{ flex: 1 }}>
                                        {uploadedFile ? (
                                            <span style={{ color: 'var(--accent-blue)', fontWeight: 'bold', fontSize: '0.9rem' }}>
                                                {uploadedFile.name}
                                            </span>
                                        ) : (
                                            <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                                                Click to upload MPPR Excel
                                            </span>
                                        )}
                                    </div>
                                    {uploadedFile && <ShieldCheck size={20} style={{ color: 'var(--status-pass)' }} />}
                                </div>
                            )}

                            {/* SharePoint file picker */}
                            {source === 'sharepoint' && (
                                <div>
                                    {spFilesLoading ? (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '0.9rem', padding: '12px 0' }}>
                                            <Loader2 size={16} className="spin" /> Loading SharePoint files...
                                        </div>
                                    ) : (
                                        <select
                                            className="form-control"
                                            value={spFileId}
                                            onChange={e => handleSpFileSelect(e.target.value)}
                                            style={{ backgroundColor: 'var(--bg-primary)' }}
                                            disabled={spProjectsLoading}
                                        >
                                            <option value="">— Select an MPPR file —</option>
                                            {spFiles.map(f => (
                                                <option key={f.file_id} value={f.file_id}>{f.name}</option>
                                            ))}
                                        </select>
                                    )}
                                    {spProjectsLoading && (
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '8px' }}>
                                            <Loader2 size={14} className="spin" /> Loading projects from file...
                                        </div>
                                    )}
                                    {spProjects && !spProjectsLoading && (
                                        <div style={{ marginTop: '8px', fontSize: '0.85rem', color: 'var(--status-pass)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                            <ShieldCheck size={14} /> {spProjects.length} projects loaded from SharePoint
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        <button
                            onClick={handleBatchValidate}
                            className="btn btn-primary"
                            style={{ width: '100%', padding: '14px' }}
                            disabled={loading || (source === 'local' ? !uploadedFile : !spProjects)}
                        >
                            {loading ? (
                                <>
                                    <Loader2 size={18} className="spin" style={{ animation: 'spin 1s linear infinite' }} />
                                    Processing Batch...
                                </>
                            ) : (
                                <>
                                    <ShieldCheck size={18} />
                                    Run AI Checks (Guidelines & Data Movement)
                                </>
                            )}
                        </button>

                        {/* Clear previous results */}
                        {results.length > 0 && !loading && (
                            <button
                                onClick={clearBatchState}
                                className="btn btn-outline"
                                style={{ width: '100%', marginTop: '10px', padding: '9px', fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
                            >
                                <RotateCcw size={14} />
                                Clear Results
                            </button>
                        )}
                    </div>
                </div>

                {/* Right Column: Results Table */}
                <div className="results-section">
                    {!results.length && !loading && (
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '400px', opacity: 0.5, padding: '60px 20px' }}>
                            <ShieldCheck size={48} style={{ marginBottom: '16px' }} />
                            <p>Upload a file and run validation to see batch results.</p>
                        </div>
                    )}

                    {loading && progress.total === 0 && (
                        <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: '400px', padding: '60px 20px' }}>
                            <Loader2 size={48} className="spin" style={{ marginBottom: '16px', color: 'var(--accent-blue)' }} />
                            <h3 style={{ marginBottom: '8px' }}>{progress.statusText || 'Processing...'}</h3>
                            <p style={{ color: 'var(--text-secondary)' }}>Reading the Excel file format...</p>
                        </div>
                    )}

                    {(results.length > 0 || (loading && progress.total > 0)) && (
                        <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

                            {/* Interrupted run banner */}
                            {isPartialRun && (
                                <div style={{
                                    display: 'flex', alignItems: 'center', gap: '10px',
                                    padding: '12px 16px', borderRadius: '8px',
                                    background: 'var(--status-warn-bg)', border: '1px solid var(--status-warn)',
                                    color: 'var(--status-warn)', fontSize: '0.88rem',
                                }}>
                                    <AlertTriangle size={16} />
                                    Previous run was interrupted ({progress.current}/{progress.total} completed). Upload the file again and re-run to finish.
                                </div>
                            )}

                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', paddingBottom: '8px', borderBottom: '1px solid var(--border-color)' }}>
                                <div>
                                    <h2 style={{ fontSize: '1.2rem', marginBottom: '4px' }}>Batch Results</h2>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                                        Period: <strong>{period}</strong> | Processed: <strong>{progress.current} / {progress.total || total}</strong>
                                    </div>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '4px' }}>
                                        Thresholds: 8-10 (Pass) &bull; 6-7 (Pass with Warning) &bull; &lt; 6 (Fail)
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                                    <div style={{ display: 'flex', gap: '12px', marginRight: '16px' }}>
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
                                    <button
                                        onClick={handleExport}
                                        disabled={results.length === 0}
                                        className="btn btn-outline"
                                        style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 12px', fontSize: '0.85rem' }}
                                    >
                                        <Download size={14} />
                                        Export Excel
                                    </button>
                                </div>
                            </div>

                            {/* Progress bar — only shown while actively loading */}
                            {(loading && progress.total > 0) && (
                                <div style={{ background: 'var(--bg-secondary)', borderRadius: '8px', padding: '16px', border: '1px solid var(--border-color)' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '0.9rem' }}>
                                        <span style={{ fontWeight: '500' }}>{progress.statusText}</span>
                                        <span style={{ color: 'var(--text-secondary)' }}>{Math.round((progress.current / progress.total) * 100)}%</span>
                                    </div>
                                    <div style={{ height: '8px', background: 'var(--border-color)', borderRadius: '4px', overflow: 'hidden' }}>
                                        <div style={{
                                            height: '100%',
                                            background: 'var(--accent-blue)',
                                            width: `${(progress.current / progress.total) * 100}%`,
                                            transition: 'width 0.3s ease'
                                        }}></div>
                                    </div>
                                </div>
                            )}

                            {results.length > 0 && (
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
                                                const verdictLabel = (v: string) => v === 'PASS' ? 'Green' : v === 'PASS_WITH_WARNINGS' ? 'Amber' : v === 'FAIL' ? 'Red' : v;
                                                const displayVerdict = res.overall_verdict;
                                                const vColor         = getVerdictColor(displayVerdict);
                                                const isExpanded     = expandedRow === res.project_name;
                                                const totalIssues    = (res.layer1?.issues?.length || 0) + (res.layer2?.issues?.length || 0);

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
                                                                    display: 'inline-flex', alignItems: 'center', gap: '6px',
                                                                    padding: '4px 8px', borderRadius: '4px',
                                                                    background: getVerdictBg(displayVerdict),
                                                                    color: vColor,
                                                                    fontWeight: 'bold', fontSize: '0.85rem'
                                                                }}>
                                                                    {getVerdictIcon(displayVerdict)}
                                                                    {verdictLabel(displayVerdict)}
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
                            )}

                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default BatchValidateView;