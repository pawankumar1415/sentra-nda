import { useState, useRef } from 'react';
import { UploadCloud, FileSpreadsheet, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';
import { ingestFile } from '../services/api';

const IngestView = () => {
    const [mpprFile, setMpprFile] = useState<File | null>(null);
    const [eacFile, setEacFile] = useState<File | null>(null);

    const [mpprLoading, setMpprLoading] = useState(false);
    const [mpprResult, setMpprResult] = useState<any>(null);
    const [mpprError, setMpprError] = useState('');

    const [eacLoading, setEacLoading] = useState(false);
    const [eacResult, setEacResult] = useState<any>(null);
    const [eacError, setEacError] = useState('');

    const mpprInputRef = useRef<HTMLInputElement>(null);
    const eacInputRef = useRef<HTMLInputElement>(null);

    const handleMpprUpload = async () => {
        if (!mpprFile) return setMpprError('Please select a file.');

        setMpprLoading(true);
        setMpprError('');
        setMpprResult(null);

        try {
            const res = await ingestFile(mpprFile, 'mppr');
            setMpprResult(res);
            setMpprFile(null);
        } catch (err: any) {
            setMpprError(err.message || 'Upload failed');
        } finally {
            setMpprLoading(false);
        }
    };

    const handleEacUpload = async () => {
        if (!eacFile) return setEacError('Please select a file.');

        setEacLoading(true);
        setEacError('');
        setEacResult(null);

        try {
            const res = await ingestFile(eacFile, 'eac');
            setEacResult(res);
            setEacFile(null);
        } catch (err: any) {
            setEacError(err.message || 'Upload failed');
        } finally {
            setEacLoading(false);
        }
    };

    return (
        <div className="ingest-view">
            <div className="page-header">
                <h1 className="page-title">Data Ingestion</h1>
                <p className="page-subtitle">Upload NDA MPPR and EAC variance spreadsheets into the knowledge base.</p>
            </div>

            <div className="layout-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '32px' }}>

                {/* MPPR Upload Card */}
                <div className="card">
                    <h2 className="card-title">Upload NDA MPPR Reference Data</h2>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px' }}>
                        Upload standard monthly period reports. The period name (e.g., P08) will be extracted automatically.
                    </p>

                    <div
                        className="upload-dropzone"
                        style={{ border: '2px dashed var(--accent-blue)', borderRadius: '12px', padding: '40px 20px', textAlign: 'center', cursor: 'pointer', transition: 'var(--transition)', background: mpprFile ? 'var(--accent-blue-glow)' : 'var(--bg-secondary)' }}
                        onClick={() => mpprInputRef.current?.click()}
                    >
                        <input
                            type="file"
                            ref={mpprInputRef}
                            style={{ display: 'none' }}
                            accept=".xlsx,.xls"
                            onChange={(e) => setMpprFile(e.target.files?.[0] || null)}
                        />

                        {mpprFile ? (
                            <div style={{ color: 'var(--accent-blue)' }}>
                                <FileSpreadsheet size={40} style={{ margin: '0 auto 16px' }} />
                                <h3 style={{ fontSize: '1rem', marginBottom: '4px' }}>{mpprFile.name}</h3>
                                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{(mpprFile.size / 1024 / 1024).toFixed(2)} MB</p>
                            </div>
                        ) : (
                            <div>
                                <UploadCloud size={40} style={{ color: 'var(--text-secondary)', margin: '0 auto 16px' }} />
                                <h3 style={{ fontSize: '1rem', marginBottom: '4px' }}>Click to select Excel file</h3>
                                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>For standard /api/ingest route</p>
                            </div>
                        )}
                    </div>

                    <div style={{ marginTop: '24px' }}>
                        <button
                            className="btn btn-primary"
                            disabled={!mpprFile || mpprLoading}
                            onClick={handleMpprUpload}
                            style={{ width: '100%' }}
                        >
                            {mpprLoading ? <><Loader2 size={16} className="spin" style={{ animation: 'spin 1s linear infinite' }} /> Processing...</> : 'Upload & Process MPPR'}
                        </button>
                    </div>

                    {mpprError && (
                        <div style={{ marginTop: '16px', color: 'var(--status-fail)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.9rem' }}>
                            <AlertTriangle size={16} /> {mpprError}
                        </div>
                    )}

                    {mpprResult && (
                        <div className="animate-fade-in" style={{ marginTop: '16px', padding: '16px', borderRadius: '8px', background: 'var(--status-pass-bg)', border: '1px solid var(--status-pass)' }}>
                            <div style={{ color: 'var(--status-pass)', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px', fontWeight: '600' }}>
                                <CheckCircle2 size={18} /> Success! Period: {mpprResult.period}
                            </div>
                            <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                                Indexed {mpprResult.indexed} project records into PostgreSQL vector DB.
                            </p>
                        </div>
                    )}
                </div>

                {/* EAC Upload Card */}
                <div className="card">
                    <h2 className="card-title">Upload EAC Variance Data</h2>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px' }}>
                        Upload the lifecycle EAC variance sheet showing major monetary shifts and delays.
                    </p>

                    <div
                        className="upload-dropzone"
                        style={{ border: '2px dashed var(--accent-indigo)', borderRadius: '12px', padding: '40px 20px', textAlign: 'center', cursor: 'pointer', transition: 'var(--transition)', background: eacFile ? 'rgba(79, 70, 229, 0.1)' : 'var(--bg-secondary)' }}
                        onClick={() => eacInputRef.current?.click()}
                    >
                        <input
                            type="file"
                            ref={eacInputRef}
                            style={{ display: 'none' }}
                            accept=".xlsx,.xls"
                            onChange={(e) => setEacFile(e.target.files?.[0] || null)}
                        />

                        {eacFile ? (
                            <div style={{ color: 'var(--accent-indigo)' }}>
                                <FileSpreadsheet size={40} style={{ margin: '0 auto 16px' }} />
                                <h3 style={{ fontSize: '1rem', marginBottom: '4px' }}>{eacFile.name}</h3>
                                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{(eacFile.size / 1024 / 1024).toFixed(2)} MB</p>
                            </div>
                        ) : (
                            <div>
                                <UploadCloud size={40} style={{ color: 'var(--text-secondary)', margin: '0 auto 16px' }} />
                                <h3 style={{ fontSize: '1rem', marginBottom: '4px' }}>Click to select Excel file</h3>
                                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>For /api/ingest-eac route</p>
                            </div>
                        )}
                    </div>

                    <div style={{ marginTop: '24px' }}>
                        <button
                            className="btn btn-secondary"
                            disabled={!eacFile || eacLoading}
                            onClick={handleEacUpload}
                            style={{ width: '100%', borderColor: 'var(--accent-indigo)', color: 'var(--accent-indigo)' }}
                        >
                            {eacLoading ? <><Loader2 size={16} className="spin" style={{ animation: 'spin 1s linear infinite' }} /> Processing...</> : 'Upload & Process EAC Data'}
                        </button>
                    </div>

                    {eacError && (
                        <div style={{ marginTop: '16px', color: 'var(--status-fail)', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.9rem' }}>
                            <AlertTriangle size={16} /> {eacError}
                        </div>
                    )}

                    {eacResult && (
                        <div className="animate-fade-in" style={{ marginTop: '16px', padding: '16px', borderRadius: '8px', background: 'var(--status-pass-bg)', border: '1px solid var(--status-pass)' }}>
                            <div style={{ color: 'var(--status-pass)', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px', fontWeight: '600' }}>
                                <CheckCircle2 size={18} /> Success!
                            </div>
                            <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                                Indexed {eacResult.indexed} EAC movement records into PostgreSQL.
                            </p>
                        </div>
                    )}
                </div>

            </div>
        </div>
    );
};

export default IngestView;
