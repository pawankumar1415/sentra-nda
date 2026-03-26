// @ts-ignore
import localSettings from '../local.settings.json';

// RAG function app (custom Python + PostgreSQL)
export const API_BASE_URL = "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api";
export const AZURE_FUNCTION_KEY = localSettings.AZURE_FUNCTION_KEY || '';

// Agent function app (Azure AI Foundry)
export const AGENT_API_BASE_URL = "https://nda-foundry-api-g3b0f6fjgzgjhbfx.uksouth-01.azurewebsites.net/api";
export const AZURE_AGENT_FUNCTION_KEY = localSettings.AZURE_AGENT_FUNCTION_KEY || AZURE_FUNCTION_KEY;

export const getAuthParams = () => {
    return AZURE_FUNCTION_KEY ? `?code=${encodeURIComponent(AZURE_FUNCTION_KEY)}` : '';
};

export const getAgentAuthParams = () => {
    return AZURE_AGENT_FUNCTION_KEY ? `?code=${encodeURIComponent(AZURE_AGENT_FUNCTION_KEY)}` : '';
};

// ── JWT auth helpers ──────────────────────────────────────────────────────────

/** Returns the stored JWT token from localStorage, or null. */
function getStoredToken(): string | null {
    try {
        const raw = localStorage.getItem('nda_auth');
        return raw ? JSON.parse(raw).token : null;
    } catch {
        return null;
    }
}

/** Returns headers that include the JWT Bearer token if available. */
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const token = getStoredToken();
    return {
        ...extra,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
}

// ── Auth API ─────────────────────────────────────────────────────────────────

export interface AuthResponse {
    token: string;
    username: string;
    is_admin: boolean;
}

export const loginUser = async (username: string, password: string): Promise<AuthResponse> => {
    const url = `${API_BASE_URL}/auth/login${getAuthParams()}`;
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Login failed (${response.status})`);
    }
    return response.json();
};

export const registerUser = async (username: string, password: string): Promise<AuthResponse> => {
    const url = `${API_BASE_URL}/auth/register${getAuthParams()}`;
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Registration failed (${response.status})`);
    }
    return response.json();
};

// ── Admin API ─────────────────────────────────────────────────────────────────

export interface UserRecord {
    user_id: string;
    username: string;
    is_admin: boolean;
    is_active: boolean;
    created_at: string | null;
    projects_count: number;
    sessions_count: number;
}

export const listUsers = async (): Promise<UserRecord[]> => {
    const url = `${API_BASE_URL}/mgmt/users${getAuthParams()}`;
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Failed to list users (${response.status})`);
    }
    const data = await response.json();
    return data.users;
};

export const updateUser = async (user_id: string, changes: { is_active?: boolean; is_admin?: boolean }): Promise<void> => {
    const url = `${API_BASE_URL}/mgmt/users/update${getAuthParams()}`;
    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ user_id, ...changes }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Update failed (${response.status})`);
    }
};

export const deleteUser = async (user_id: string): Promise<void> => {
    const url = `${API_BASE_URL}/mgmt/users/delete${getAuthParams()}`;
    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ user_id }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Delete failed (${response.status})`);
    }
};

export interface ValidateRequest {
    narrative: string;
    project_name: string;
    period: string;
}

export const validateNarrative = async (data: ValidateRequest) => {
    const url = `${API_BASE_URL}/validate${getAuthParams()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            narrative: data.narrative,
            project_name: data.project_name,
            period: data.period,
            top_k: 5
        }),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};

export const ingestFile = async (file: File, type: 'mppr' | 'eac') => {
    const endpoint = type === 'mppr' ? 'ingest' : 'ingest-eac';

    // Create URLSearchParams to securely handle query parameters
    const params = new URLSearchParams();
    if (AZURE_FUNCTION_KEY) params.append('code', AZURE_FUNCTION_KEY);
    params.append('filename', file.name);

    const url = `${API_BASE_URL}/${endpoint}?${params.toString()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/octet-stream' }),
        body: file,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};

export interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
}

/**
 * Metadata returned by the server with each chat response.
 * Useful for debugging and displaying intent detection results.
 */
export interface ChatMeta {
    intent: string;
    projects_detected: string[];
    context_length: number;
    /** True when the server created a brand-new session for this call */
    is_new_session: boolean;
}

/**
 * Full response shape from POST /api/chat.
 */
export interface ChatResponse {
    answer: string;
    /**
     * Server-assigned session UUID.  Persist this in localStorage and send it
     * back on every subsequent message to maintain conversation continuity.
     * When null the server will always create a new session.
     */
    session_id: string;
    meta: ChatMeta;
}

/**
 * Send a chat message to the RAG agent.
 *
 * @param question   The user's question for this turn.
 * @param sessionId  UUID from a previous response (stored in localStorage).
 *                   Pass null on the very first message or when starting a
 *                   new conversation — the server will create a fresh session.
 * @param history    Legacy fallback: full history array sent by the client.
 *                   Only used when sessionId is null/missing (old behaviour).
 */
export const sendChatMessage = async (
    question: string,
    sessionId: string | null,
    history: ChatMessage[] = [],
): Promise<ChatResponse> => {
    const url = `${API_BASE_URL}/chat${getAuthParams()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
            question,
            session_id: sessionId,   // null on first message → server creates session
            history,                 // ignored by server when session_id is valid
        }),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json() as Promise<ChatResponse>;
};

export interface ProjectInfo {
    project_name: string;
    narrative_text: string;
}

export interface ListProjectsResponse {
    period: string;
    projects: ProjectInfo[];
}

export const listProjects = async (file: File): Promise<ListProjectsResponse> => {
    const params = new URLSearchParams();
    if (AZURE_FUNCTION_KEY) params.append('code', AZURE_FUNCTION_KEY);
    params.append('filename', file.name);

    const url = `${API_BASE_URL}/list-projects?${params.toString()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/octet-stream' }),
        body: file,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};

export interface SearchedProject {
    project_name: string;
    period_short_name: string;
    narrative_text: string;
}

/**
 * Search indexed projects in the PGVector database by name.
 * Returns projects whose names contain the query string (case-insensitive).
 * Lets the user populate the validate form without uploading an Excel file first.
 */
export const searchProjects = async (query: string, limit = 20): Promise<SearchedProject[]> => {
    if (!query || query.length < 2) return [];
    const params = new URLSearchParams();
    if (AZURE_FUNCTION_KEY) params.append('code', AZURE_FUNCTION_KEY);
    params.append('q', query);
    params.append('limit', String(limit));

    const url = `${API_BASE_URL}/search-projects?${params.toString()}`;
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) return [];
    const data = await response.json();
    return data.projects || [];
};

/**
 * Search indexed projects in Azure AI Search (agent backend) by name.
 * Used by ValidateView when in Agent/Foundry mode.
 */
export const searchProjectsAgent = async (query: string, limit = 20): Promise<SearchedProject[]> => {
    if (!query || query.length < 2) return [];
    const params = new URLSearchParams();
    if (AZURE_AGENT_FUNCTION_KEY) params.append('code', AZURE_AGENT_FUNCTION_KEY);
    params.append('q', query);
    params.append('limit', String(limit));

    const url = `${AGENT_API_BASE_URL}/search-projects?${params.toString()}`;
    const response = await fetch(url);
    if (!response.ok) return [];
    const data = await response.json();
    return data.projects || [];
};

export interface AgentValidateRequest {
    narrative: string;
    project_name: string;
    period: string;
    conversation_id?: string | null;
}

export interface AgentValidateResponse {
    conversation_id: string;
    is_new_conversation: boolean;
    validation_result: string;
}

/**
 * Validate a single narrative using the Azure AI Foundry agent.
 */
export const validateNarrativeAgent = async (data: AgentValidateRequest): Promise<AgentValidateResponse> => {
    const url = `${AGENT_API_BASE_URL}/validate${getAgentAuthParams()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            narrative:       data.narrative,
            project_name:    data.project_name,
            period:          data.period,
            conversation_id: data.conversation_id || null,
        }),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Agent API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};

export const batchValidate = async (file: File) => {
    const params = new URLSearchParams();
    if (AZURE_FUNCTION_KEY) params.append('code', AZURE_FUNCTION_KEY);
    params.append('filename', file.name);

    const url = `${API_BASE_URL}/batch-validate?${params.toString()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/octet-stream' }),
        body: file,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};
