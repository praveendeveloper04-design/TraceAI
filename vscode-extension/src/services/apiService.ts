/**
 * TraceAI API Service
 *
 * Handles all communication between the VS Code extension and the
 * TraceAI Python backend API server.
 */

import axios, { AxiosInstance } from 'axios';

export interface TaskAnalyzerStatus {
    version: string;
    configured: boolean;
    ticket_source: string | null;
    repositories: number;
    connectors: number;
    profiles: number;
}

export interface TaskItem {
    id: string;
    source: string;
    external_id: string;
    title: string;
    description: string;
    task_type: string;
    status: string;
    severity: string;
    assigned_to: string | null;
    created_at: string | null;
    tags: string[];
}

export interface InvestigationReport {
    id: string;
    task_id: string;
    task_title: string;
    status: string;
    started_at: string;
    completed_at: string | null;
    summary: string;
    root_cause: string;
    findings: InvestigationFinding[];
    recommendations: string[];
    affected_files: string[];
    affected_services: string[];
    error: string | null;
    investigation_graph: Record<string, unknown> | null;
    root_cause_hypotheses: Array<{
        description: string;
        evidence: string[];
        confidence: number;
    }> | null;
    evidence_summary: Record<string, unknown> | null;
}

export interface InvestigationFinding {
    category: string;
    title: string;
    description: string;
    confidence: number;
    evidence: string[];
    file_references: string[];
}

export interface InvestigationSummary {
    id: string;
    task_id: string;
    task_title: string;
    status: string;
    started_at: string;
}

export class ApiService {
    private client: AxiosInstance;

    constructor(port: number = 7420) {
        this.client = axios.create({
            baseURL: `http://127.0.0.1:${port}`,
            timeout: 120000, // 2 minutes for investigations
            headers: { 'Content-Type': 'application/json' },
        });
    }

    async getStatus(): Promise<TaskAnalyzerStatus> {
        const resp = await this.client.get('/api/status');
        return resp.data;
    }

    async healthCheck(): Promise<boolean> {
        try {
            const resp = await this.client.get('/api/health', { timeout: 3000 });
            return resp.data?.status === 'ok';
        } catch {
            return false;
        }
    }

    async fetchTasks(
        assignedTo?: string,
        query?: string,
        maxResults: number = 50,
        statuses?: string[],
        workspacePath?: string,
    ): Promise<TaskItem[]> {
        const resp = await this.client.post('/api/tasks', {
            assigned_to: assignedTo || null,
            query: query || null,
            max_results: maxResults,
            statuses: statuses || null,
            workspace_path: workspacePath || null,
        });
        return resp.data;
    }

    async getTask(taskId: string): Promise<TaskItem> {
        const resp = await this.client.get(`/api/tasks/${taskId}`);
        return resp.data;
    }

    async investigate(taskId: string): Promise<InvestigationReport> {
        const resp = await this.client.post('/api/investigate', { task_id: taskId });
        return resp.data;
    }

    async listInvestigations(limit: number = 20): Promise<InvestigationSummary[]> {
        const resp = await this.client.get('/api/investigations', { params: { limit } });
        return resp.data;
    }

    async getInvestigation(reportId: string): Promise<InvestigationReport> {
        const resp = await this.client.get(`/api/investigations/${reportId}`);
        return resp.data;
    }

    async getInvestigationMarkdown(reportId: string): Promise<string> {
        const resp = await this.client.get(`/api/investigations/${reportId}/markdown`);
        return resp.data.markdown;
    }

    async isServerRunning(): Promise<boolean> {
        try {
            await this.getStatus();
            return true;
        } catch {
            return false;
        }
    }
}
