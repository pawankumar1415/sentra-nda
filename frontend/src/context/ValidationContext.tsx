import { createContext, useContext, useState, useCallback } from 'react';
import type { ReactNode } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

export interface HistoryEntry {
    id: string;
    type: 'individual' | 'batch';
    project_name: string;
    period: string;
    verdict: 'PASS' | 'WARN' | 'FAIL' | 'ERROR';
    score: number | null;
    timestamp: string; // ISO 8601
    batch_id?: string; // links all projects from the same batch run
}

export interface BatchState {
    results: any[];
    total: number;
    period: string;
    progress: { current: number; total: number; statusText: string };
}

export interface ProjectEntry {
    project_name: string;
    narrative_text: string;
}

export interface ValidateFormState {
    result: any | null;
    projectName: string;
    period: string;
    narrative: string;
    projectsList: ProjectEntry[];
    isManualEntry: boolean;
}

// ── Storage keys ─────────────────────────────────────────────────────────────

const HISTORY_KEY  = 'nda_validation_history';
const BATCH_KEY    = 'nda_batch_state';
const VALIDATE_KEY = 'nda_validate_state';
const MAX_HISTORY  = 500;

// ── Defaults ─────────────────────────────────────────────────────────────────

const DEFAULT_BATCH: BatchState = {
    results:  [],
    total:    0,
    period:   '',
    progress: { current: 0, total: 0, statusText: '' },
};

const DEFAULT_VALIDATE: ValidateFormState = {
    result:       null,
    projectName:  'Sellafield',
    period:       'P08',
    narrative:    '',
    projectsList: [],
    isManualEntry: true,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function loadFromStorage<T>(key: string, fallback: T): T {
    try {
        const raw = localStorage.getItem(key);
        return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
        return fallback;
    }
}

function saveToStorage(key: string, value: unknown): void {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // localStorage full or unavailable — fail silently
    }
}

export function generateId(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = (Math.random() * 16) | 0;
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
}

// ── Context ───────────────────────────────────────────────────────────────────

interface ValidationContextValue {
    // Batch validate state (persisted)
    batchState: BatchState;
    setBatchState: (updater: BatchState | ((prev: BatchState) => BatchState)) => void;
    clearBatchState: () => void;

    // Individual validate state (persisted)
    validateState: ValidateFormState;
    setValidateState: (updater: ValidateFormState | ((prev: ValidateFormState) => ValidateFormState)) => void;

    // History log (persisted, newest first)
    history: HistoryEntry[];
    addToHistory: (entries: Omit<HistoryEntry, 'id' | 'timestamp'>[]) => void;
    clearHistory: () => void;
}

const ValidationContext = createContext<ValidationContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────────────

export function ValidationProvider({ children }: { children: ReactNode }) {
    const [batchState, setBatchStateRaw] = useState<BatchState>(
        () => loadFromStorage(BATCH_KEY, DEFAULT_BATCH)
    );
    const [validateState, setValidateStateRaw] = useState<ValidateFormState>(
        () => loadFromStorage(VALIDATE_KEY, DEFAULT_VALIDATE)
    );
    const [history, setHistory] = useState<HistoryEntry[]>(
        () => loadFromStorage(HISTORY_KEY, [])
    );

    const setBatchState = useCallback(
        (updater: BatchState | ((prev: BatchState) => BatchState)) => {
            setBatchStateRaw(prev => {
                const next = typeof updater === 'function' ? updater(prev) : updater;
                saveToStorage(BATCH_KEY, next);
                return next;
            });
        },
        []
    );

    const clearBatchState = useCallback(() => {
        saveToStorage(BATCH_KEY, DEFAULT_BATCH);
        setBatchStateRaw(DEFAULT_BATCH);
    }, []);

    const setValidateState = useCallback(
        (updater: ValidateFormState | ((prev: ValidateFormState) => ValidateFormState)) => {
            setValidateStateRaw(prev => {
                const next = typeof updater === 'function' ? updater(prev) : updater;
                saveToStorage(VALIDATE_KEY, next);
                return next;
            });
        },
        []
    );

    const addToHistory = useCallback(
        (entries: Omit<HistoryEntry, 'id' | 'timestamp'>[]) => {
            const now = new Date().toISOString();
            const newEntries: HistoryEntry[] = entries.map((e, i) => ({
                ...e,
                id: `${Date.now()}-${i}`,
                timestamp: now,
            }));
            setHistory(prev => {
                const next = [...newEntries, ...prev].slice(0, MAX_HISTORY);
                saveToStorage(HISTORY_KEY, next);
                return next;
            });
        },
        []
    );

    const clearHistory = useCallback(() => {
        setHistory([]);
        try { localStorage.removeItem(HISTORY_KEY); } catch {}
    }, []);

    return (
        <ValidationContext.Provider
            value={{
                batchState, setBatchState, clearBatchState,
                validateState, setValidateState,
                history, addToHistory, clearHistory,
            }}
        >
            {children}
        </ValidationContext.Provider>
    );
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useValidation(): ValidationContextValue {
    const ctx = useContext(ValidationContext);
    if (!ctx) throw new Error('useValidation must be used inside ValidationProvider');
    return ctx;
}