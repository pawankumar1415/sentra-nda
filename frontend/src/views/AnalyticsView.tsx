import { useState } from 'react';
import { BarChart2, CheckCircle2, AlertTriangle, XCircle, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import { useValidation, type HistoryEntry } from '../context/ValidationContext';

const PAGE_SIZE = 25;

const TRUNCATE = 100; // chars shown in table cell before truncation

const truncate = (text: string | undefined, len = TRUNCATE) =>
    !text ? '—' : text.length <= len ? text : text.slice(0, len) + '…';

const AnalyticsView = () => {
    const { history, clearHistory } = useValidation();
    const [confirmClear, setConfirmClear] = useState(false);
    const [page, setPage] = useState(1);
    const [filterVerdict, setFilterVerdict] = useState<'ALL' | 'PASS' | 'WARN' | 'FAIL' | 'ERROR'>('ALL');
    const [sortDesc, setSortDesc] = useState(true);
    const [expandedRow, setExpandedRow] = useState<string | null>(null);

    // ── Derived stats ─────────────────────────────────────────────────────────

    const total           = history.length;
    const passCount       = history.filter(e => e.verdict === 'PASS').length;
    const warnCount       = history.filter(e => e.verdict === 'WARN' || (e.verdict as string) === 'PASS_WITH_WARNINGS').length;
    const failCount       = history.filter(e => e.verdict === 'FAIL').length;
    const individualCount = history.filter(e => e.type === 'individual').length;
    const batchCount      = history.filter(e => e.type === 'batch').length;

    const passRate   = total > 0 ? Math.round((passCount / total) * 100) : 0;
    const warnRate   = total > 0 ? Math.round((warnCount / total) * 100) : 0;
    const failRate   = total > 0 ? 100 - passRate - warnRate : 0;

    // ── Filtered + sorted rows ────────────────────────────────────────────────

    const filtered = history
        .filter(e => filterVerdict === 'ALL' || e.verdict === filterVerdict)
        .slice() // don't mutate
        .sort((a, b) => {
            const diff = new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
            return sortDesc ? diff : -diff;
        });

    const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
    const pageRows   = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

    // ── Helpers ───────────────────────────────────────────────────────────────

    const verdictColor = (v: string) => {
        if (v === 'PASS')  return 'var(--status-pass)';
        if (v === 'FAIL')  return 'var(--status-fail)';
        if (v === 'ERROR') return 'var(--status-fail)';
        return 'var(--status-warn)'; // WARN + PASS_WITH_WARNINGS
    };

    const verdictBg = (v: string) => {
        if (v === 'PASS')  return 'var(--status-pass-bg)';
        if (v === 'FAIL')  return 'var(--status-fail-bg)';
        if (v === 'ERROR') return 'var(--status-fail-bg)';
        return 'var(--status-warn-bg)'; // WARN + PASS_WITH_WARNINGS
    };

    const verdictIcon = (v: string) => {
        if (v === 'PASS')  return <CheckCircle2 size={14} />;
        if (v === 'FAIL' || v === 'ERROR') return <XCircle size={14} />;
        return <AlertTriangle size={14} />;
    };

    const formatDate = (iso: string) =>
        new Date(iso).toLocaleDateString('en-GB', {
            day:   '2-digit',
            month: 'short',
            year:  'numeric',
        });

    const formatTime = (iso: string) =>
        new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

    // ── Clear handler ─────────────────────────────────────────────────────────

    const handleClear = () => {
        if (!confirmClear) { setConfirmClear(true); return; }
        clearHistory();
        setConfirmClear(false);
        setPage(1);
    };

    // ── Summary cards data ────────────────────────────────────────────────────

    const cards = [
        { label: 'TOTAL PROJECTS', value: total,           sub: 'narratives',            color: 'var(--text-primary)'  },
        { label: 'GREEN',          value: passCount,        sub: 'score ≥ 9',             color: 'var(--status-pass)'   },
        { label: 'AMBER',          value: warnCount,        sub: 'score 7–8',             color: 'var(--status-warn)'   },
        { label: 'RED',            value: failCount,        sub: 'score < 7',             color: 'var(--status-fail)'   },
        { label: 'INDIVIDUAL',     value: individualCount,  sub: 'single validations',    color: 'var(--accent-blue)'   },
        { label: 'BATCH',          value: batchCount,       sub: 'batch project entries', color: 'var(--text-secondary)'},
    ];

    return (
        <div className="page-container">
            {/* Header */}
            <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                    <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <BarChart2 size={28} style={{ color: 'var(--accent-blue)' }} />
                        Analytics
                    </h1>
                    <p className="page-subtitle">Overview of your narrative scoring activity.</p>
                </div>
                {history.length > 0 && (
                    <button
                        onClick={handleClear}
                        className={confirmClear ? 'btn btn-primary' : 'btn btn-outline'}
                        style={{
                            display: 'flex', alignItems: 'center', gap: '6px',
                            fontSize: '0.82rem', padding: '7px 14px',
                            ...(confirmClear ? { background: 'var(--status-fail)', borderColor: 'var(--status-fail)' } : {}),
                        }}
                        onBlur={() => setConfirmClear(false)}
                    >
                        <Trash2 size={14} />
                        {confirmClear ? 'Confirm Clear History' : 'Clear History'}
                    </button>
                )}
            </div>

            {/* Summary cards */}
            <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(6, 1fr)',
                gap: '16px',
                marginBottom: '24px',
            }}>
                {cards.map(card => (
                    <div key={card.label} className="card" style={{ padding: '20px 16px', textAlign: 'center' }}>
                        <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '1px', color: 'var(--text-secondary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                            {card.label}
                        </div>
                        <div style={{ fontSize: '2rem', fontWeight: 800, color: card.color, lineHeight: 1 }}>
                            {card.value}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
                            {card.sub}
                        </div>
                    </div>
                ))}
            </div>

            {/* Pass rate bar */}
            {total > 0 && (
                <div className="card" style={{ marginBottom: '24px', padding: '20px 24px' }}>
                    <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '12px' }}>Pass Rate</h3>
                    <div style={{ height: '14px', borderRadius: '7px', overflow: 'hidden', display: 'flex', marginBottom: '10px' }}>
                        {passRate > 0 && (
                            <div style={{ width: `${passRate}%`, background: 'var(--status-pass)', transition: 'width 0.5s ease' }} title={`Pass ${passRate}%`} />
                        )}
                        {warnRate > 0 && (
                            <div style={{ width: `${warnRate}%`, background: 'var(--status-warn)', transition: 'width 0.5s ease' }} title={`Warn ${warnRate}%`} />
                        )}
                        {failRate > 0 && (
                            <div style={{ width: `${failRate}%`, background: 'var(--status-fail)', transition: 'width 0.5s ease' }} title={`Fail ${failRate}%`} />
                        )}
                    </div>
                    <div style={{ display: 'flex', gap: '20px', fontSize: '0.82rem' }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--status-pass)', display: 'inline-block' }} />
                            Green {passRate}%
                        </span>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--status-warn)', display: 'inline-block' }} />
                            Amber {warnRate}%
                        </span>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--status-fail)', display: 'inline-block' }} />
                            Red {failRate}%
                        </span>
                    </div>
                </div>
            )}

            {/* Recent scores table */}
            <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                {/* Table header row with controls */}
                <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '16px 20px', borderBottom: '1px solid var(--border-color)',
                }}>
                    <h3 style={{ fontSize: '0.95rem', fontWeight: 600, margin: 0 }}>
                        Recent Scores
                        {filtered.length !== total && (
                            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginLeft: '8px' }}>
                                ({filtered.length} of {total})
                            </span>
                        )}
                    </h3>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        {/* Verdict filter */}
                        <select
                            className="form-control"
                            value={filterVerdict}
                            onChange={e => { setFilterVerdict(e.target.value as any); setPage(1); }}
                            style={{ fontSize: '0.82rem', padding: '5px 10px', background: 'var(--bg-primary)', width: 'auto' }}
                        >
                            <option value="ALL">All verdicts</option>
                            <option value="PASS">Green only</option>
                            <option value="WARN">Amber only</option>
                            <option value="FAIL">Red only</option>
                            <option value="ERROR">Error only</option>
                        </select>
                        {/* Sort toggle */}
                        <button
                            onClick={() => { setSortDesc(p => !p); setPage(1); }}
                            className="btn btn-outline"
                            style={{ fontSize: '0.82rem', padding: '5px 10px', display: 'flex', alignItems: 'center', gap: '4px' }}
                            title={sortDesc ? 'Newest first' : 'Oldest first'}
                        >
                            {sortDesc ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
                            {sortDesc ? 'Newest' : 'Oldest'}
                        </button>
                    </div>
                </div>

                {history.length === 0 ? (
                    <div style={{ padding: '60px 20px', textAlign: 'center', color: 'var(--text-secondary)', opacity: 0.6 }}>
                        <BarChart2 size={40} style={{ marginBottom: '12px', margin: '0 auto 12px' }} />
                        <p>No validation history yet. Run a narrative validation or batch to see results here.</p>
                    </div>
                ) : filtered.length === 0 ? (
                    <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--text-secondary)', opacity: 0.6 }}>
                        <p>No entries match the selected filter.</p>
                    </div>
                ) : (
                    <>
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <thead>
                                    <tr style={{ background: 'var(--bg-secondary)', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--text-secondary)' }}>
                                        <th style={{ padding: '10px 20px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }}>ID</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }}>Document</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }}>Type</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'center', fontWeight: 600, whiteSpace: 'nowrap' }}>Verdict</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'center', fontWeight: 600, whiteSpace: 'nowrap' }}>Score</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'left', fontWeight: 600 }}>Narrative</th>
                                        <th style={{ padding: '10px 16px', textAlign: 'left', fontWeight: 600 }}>Rewritten</th>
                                        <th style={{ padding: '10px 20px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>Date / Time</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {pageRows.map((entry: HistoryEntry) => {
                                        const isExpanded = expandedRow === entry.id;
                                        return (
                                            <>
                                                <tr
                                                    key={entry.id}
                                                    style={{ borderBottom: isExpanded ? 'none' : '1px solid var(--border-color)', fontSize: '0.88rem', cursor: 'pointer' }}
                                                    className="hover-row"
                                                    onClick={() => setExpandedRow(isExpanded ? null : entry.id)}
                                                >
                                                    {/* ID: period | project_name */}
                                                    <td style={{ padding: '12px 20px', fontFamily: 'monospace', fontSize: '0.82rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                                                        {entry.period} | {entry.project_name}
                                                    </td>
                                                    {/* Document */}
                                                    <td style={{ padding: '12px 16px', fontWeight: 500, maxWidth: '200px' }}>
                                                        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                            {entry.period} | {entry.project_name}
                                                        </span>
                                                    </td>
                                                    {/* Type badge */}
                                                    <td style={{ padding: '12px 16px' }}>
                                                        <span style={{
                                                            fontSize: '0.72rem', fontWeight: 600, padding: '2px 8px',
                                                            borderRadius: '4px', textTransform: 'uppercase', letterSpacing: '0.5px',
                                                            background: entry.type === 'batch' ? 'rgba(99,102,241,0.12)' : 'rgba(14,165,233,0.12)',
                                                            color: entry.type === 'batch' ? '#818cf8' : '#38bdf8',
                                                        }}>
                                                            {entry.type === 'batch' ? 'Batch' : 'Individual'}
                                                        </span>
                                                    </td>
                                                    {/* Verdict */}
                                                    <td style={{ padding: '12px 16px', textAlign: 'center' }}>
                                                        <span style={{
                                                            display: 'inline-flex', alignItems: 'center', gap: '5px',
                                                            padding: '3px 10px', borderRadius: '4px', fontWeight: 700,
                                                            fontSize: '0.82rem',
                                                            background: verdictBg(entry.verdict),
                                                            color: verdictColor(entry.verdict),
                                                        }}>
                                                            {verdictIcon(entry.verdict)}
                                                            {entry.verdict === 'PASS' ? 'Green' : (entry.verdict === 'WARN' || (entry.verdict as string) === 'PASS_WITH_WARNINGS') ? 'Amber' : entry.verdict === 'FAIL' ? 'Red' : entry.verdict}
                                                        </span>
                                                    </td>
                                                    {/* Score */}
                                                    <td style={{ padding: '12px 16px', textAlign: 'center', fontWeight: 700, fontSize: '1rem', color: entry.score !== null ? verdictColor(entry.verdict) : 'var(--text-secondary)' }}>
                                                        {entry.score !== null ? `${entry.score}` : '—'}
                                                    </td>
                                                    {/* Narrative preview */}
                                                    <td style={{ padding: '12px 16px', maxWidth: '220px', color: 'var(--text-secondary)', fontSize: '0.82rem' }}>
                                                        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={entry.narrative}>
                                                            {truncate(entry.narrative)}
                                                        </span>
                                                    </td>
                                                    {/* Rewritten preview */}
                                                    <td style={{ padding: '12px 16px', maxWidth: '220px', color: 'var(--text-secondary)', fontSize: '0.82rem' }}>
                                                        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={entry.rewritten_narrative}>
                                                            {truncate(entry.rewritten_narrative)}
                                                        </span>
                                                    </td>
                                                    {/* Date */}
                                                    <td style={{ padding: '12px 20px', textAlign: 'right', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                                                        <span style={{ display: 'block', fontSize: '0.85rem' }}>{formatDate(entry.timestamp)}</span>
                                                        <span style={{ display: 'block', fontSize: '0.75rem', opacity: 0.7 }}>{formatTime(entry.timestamp)}</span>
                                                    </td>
                                                </tr>
                                                {isExpanded && (
                                                    <tr key={`${entry.id}-expanded`} style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-secondary)' }}>
                                                        <td colSpan={8} style={{ padding: '16px 24px' }}>
                                                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                                                                <div>
                                                                    <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                                                        Narrative
                                                                    </div>
                                                                    <div style={{ fontSize: '0.85rem', lineHeight: 1.6, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                                                                        {entry.narrative || <span style={{ opacity: 0.5 }}>No narrative recorded</span>}
                                                                    </div>
                                                                </div>
                                                                <div>
                                                                    <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                                                        Rewritten Narrative
                                                                    </div>
                                                                    <div style={{ fontSize: '0.85rem', lineHeight: 1.6, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                                                                        {entry.rewritten_narrative || <span style={{ opacity: 0.5 }}>No rewrite available</span>}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )}
                                            </>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>

                        {/* Pagination */}
                        {totalPages > 1 && (
                            <div style={{
                                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                padding: '12px 20px', borderTop: '1px solid var(--border-color)',
                                fontSize: '0.83rem', color: 'var(--text-secondary)',
                            }}>
                                <span>
                                    Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filtered.length)} of {filtered.length}
                                </span>
                                <div style={{ display: 'flex', gap: '6px' }}>
                                    <button
                                        className="btn btn-outline"
                                        style={{ padding: '4px 12px', fontSize: '0.82rem' }}
                                        disabled={page === 1}
                                        onClick={() => setPage(p => p - 1)}
                                    >
                                        Previous
                                    </button>
                                    <button
                                        className="btn btn-outline"
                                        style={{ padding: '4px 12px', fontSize: '0.82rem' }}
                                        disabled={page === totalPages}
                                        onClick={() => setPage(p => p + 1)}
                                    >
                                        Next
                                    </button>
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
};

export default AnalyticsView;