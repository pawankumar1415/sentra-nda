// @ts-ignore
import localSettings from '../local.settings.json';

export const API_BASE_URL = "https://nda-python-backend-hyfdfwc2cwgzfrc6.uksouth-01.azurewebsites.net/api";
export const AZURE_FUNCTION_KEY = localSettings.AZURE_FUNCTION_KEY || '';

export const getAuthParams = () => {
    return AZURE_FUNCTION_KEY ? `?code=${encodeURIComponent(AZURE_FUNCTION_KEY)}` : '';
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
        headers: {
            'Content-Type': 'application/json',
        },
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
        headers: {
            'Content-Type': 'application/octet-stream',
        },
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

export const sendChatMessage = async (question: string, history: ChatMessage[]) => {
    const url = `${API_BASE_URL}/chat${getAuthParams()}`;

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            question,
            history
        }),
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
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
        headers: {
            'Content-Type': 'application/octet-stream',
        },
        body: file,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
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
        headers: {
            'Content-Type': 'application/octet-stream',
        },
        body: file,
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API Error (${response.status}): ${errorText}`);
    }

    return response.json();
};
