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

export interface InvestigationStatus {
    id: string;
    task_id: string;
    task_title: string;
    status: string;
    step: string;
    progress: number;
    logs: Array<{ time: string; message: string }>;
    started_at: string;
    finished_at: string | null;
}

export class ApiService {
    private client: AxiosInstance;

    constructor(port: number = 7420) {
        this.client = axios.create({
            baseURL: `http://127.0.0.1:${port}`,
            timeout: 120000, // 2 minutes default — Azure CLI token acquisition can be slow
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
        const resp = await this.client.post('/api/investigate', { task_id: taskId }, {
            timeout: 600000, // 10 minutes — investigations involve multi-layer analysis + LLM reasoning
        });
        return resp.data;
    }

    /**
     * Subscribe to live investigation progress via SSE.
     * Uses Node.js http module for true chunk-by-chunk streaming.
     * Axios buffers the entire response, so it cannot stream SSE events.
     * Falls back to blocking POST /api/investigate if SSE connection fails.
     */
    async investigateWithProgress(
        taskId: string,
        onProgress: (stage: string, message: string, percentage: number) => void,
        onComplete: (report: InvestigationReport) => void,
        onError: (error: string) => void,
    ): Promise<void> {
        const http = require('http');
        const baseUrl = this.client.defaults.baseURL || 'http://127.0.0.1:7420';
        const port = parseInt(baseUrl.split(':').pop()?.replace('/', '') || '7420', 10);
        const path = `/api/investigate/${taskId}/stream`;

        return new Promise<void>((resolve) => {
            let completed = false;
            const finish = () => { if (!completed) { completed = true; resolve(); } };

            const req = http.get({ hostname: '127.0.0.1', port, path, timeout: 600000 }, (res: any) => {
                let buffer = '';

                res.on('data', (chunk: Buffer) => {
                    buffer += chunk.toString();

                    // Parse complete SSE events (separated by double newline)
                    const parts = buffer.split('\n\n');
                    buffer = parts.pop() || '';

                    for (const part of parts) {
                        if (!part.trim()) { continue; }
                        let eventType = '';
                        let eventData = '';

                        for (const line of part.split('\n')) {
                            if (line.startsWith('event: ')) {
                                eventType = line.substring(7).trim();
                            } else if (line.startsWith('data: ')) {
                                eventData = line.substring(6).trim();
                            }
                        }

                        if (!eventType || !eventData) { continue; }

                        try {
                            if (eventType === 'progress') {
                                const parsed = JSON.parse(eventData);
                                onProgress(
                                    parsed.stage || '',
                                    parsed.message || '',
                                    parsed.progress || 50,
                                );
                            } else if (eventType === 'complete') {
                                const report = JSON.parse(eventData) as InvestigationReport;
                                onComplete(report);
                            } else if (eventType === 'error') {
                                const err = JSON.parse(eventData);
                                onError(err.error || 'Unknown error');
                            }
                        } catch { /* skip malformed JSON */ }
                    }
                });

                res.on('end', finish);
                res.on('error', () => {
                    this.investigate(taskId)
                        .then(report => { onComplete(report); finish(); })
                        .catch(err => { onError(`Investigation failed: ${err}`); finish(); });
                });
            });

            req.on('error', () => {
                // SSE connection failed — fall back to blocking POST
                this.investigate(taskId)
                    .then(report => { onComplete(report); finish(); })
                    .catch(err => { onError(`Investigation failed: ${err}`); finish(); });
            });

            req.on('timeout', () => {
                req.destroy();
                onError('Investigation timed out after 10 minutes');
                finish();
            });
        });
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

    async deleteInvestigation(reportId: string): Promise<void> {
        await this.client.delete(`/api/investigations/${reportId}`);
    }

    async deleteAllInvestigations(): Promise<{ deleted: number }> {
        const resp = await this.client.delete('/api/investigations');
        return resp.data;
    }

    async getInvestigationStatus(investigationId: string): Promise<InvestigationStatus> {
        const resp = await this.client.get(`/api/investigation/${investigationId}/status`);
        return resp.data;
    }

    async cancelInvestigation(investigationId: string): Promise<void> {
        await this.client.post(`/api/investigation/${investigationId}/cancel`);
    }

    async isServerRunning(): Promise<boolean> {
        try {
            await this.getStatus();
            return true;
        } catch {
            return false;
        }
    }

    async validateSystem(): Promise<Array<{ component: string; ok: boolean; message: string; details: string }>> {
        const resp = await this.client.get('/api/validate');
        return resp.data;
    }

    async generatePatch(investigationId: string, workspacePath?: string): Promise<PatchResult> {
        const resp = await this.client.post('/api/generate-patch', {
            investigation_id: investigationId,
            workspace_path: workspacePath || null,
        }, {
            timeout: 300000, // 5 minutes — Claude generates code patches
        });
        return resp.data;
    }
}

export interface PatchFile {
    path: string;
    description: string;
    original: string;
    patched: string;
}

export interface PatchResult {
    investigation_id: string;
    task_title: string;
    files: PatchFile[];
    raw_response?: string;
    parse_error?: string;
}
