/**
 * TraceAI -- VS Code Extension Entry Point
 *
 * Features:
 *   - Multi-tab investigations (each task gets its own panel)
 *   - Live progress with animated stages
 *   - Cancel running investigations
 *   - Re-run investigations
 *   - Delete single or all investigation history
 *   - Auto-bootstrap Python backend
 *   - Background task refresh every 5 minutes
 */

import * as vscode from 'vscode';
import { ApiService, TaskItem, InvestigationSummary } from './services/apiService';
import { ServerManager } from './services/serverManager';
import { StateManager } from './services/stateManager';
import { TaskCache } from './services/taskCache';
import { PanelManager } from './services/panelManager';
import { TaskTreeProvider } from './providers/taskTreeProvider';
import { InvestigationTreeProvider } from './providers/investigationTreeProvider';

let apiService: ApiService;
let serverManager: ServerManager;
let stateManager: StateManager;
let taskCache: TaskCache;
let panelManager: PanelManager;
let taskTreeProvider: TaskTreeProvider;
let investigationTreeProvider: InvestigationTreeProvider;
let statusBarItem: vscode.StatusBarItem;
let refreshInterval: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext): void {
    const config = vscode.workspace.getConfiguration('traceai');
    const port = config.get<number>('serverPort', 7420);

    // Initialize services
    apiService = new ApiService(port);
    serverManager = new ServerManager(port, context.extensionPath);
    stateManager = new StateManager(context.globalState);
    taskCache = new TaskCache();
    panelManager = new PanelManager(context.extensionUri, apiService);
    taskTreeProvider = new TaskTreeProvider(apiService);
    investigationTreeProvider = new InvestigationTreeProvider(apiService);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'traceai.showStatus';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Tree views
    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('traceai.tasks', taskTreeProvider),
        vscode.window.registerTreeDataProvider('traceai.investigations', investigationTreeProvider),
    );

    // ── Commands ─────────────────────────────────────────────────────────

    // Setup
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.setup', async () => {
            const terminal = vscode.window.createTerminal('TraceAI Setup');
            terminal.show();
            const venvPython = serverManager.getVenvPython();
            terminal.sendText(`& "${venvPython}" -m task_analyzer.cli.main setup`);
        }),
    );

    // Refresh tasks
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.refresh', () => refreshTasks()),
        vscode.commands.registerCommand('traceai.refreshTasks', () => refreshTasks()),
    );

    // Investigate from command palette
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.investigate', async () => {
            const taskId = await vscode.window.showInputBox({
                prompt: 'Enter the task ID to investigate',
                placeHolder: 'e.g., 12345 or PROJ-123',
            });
            if (taskId) { await investigateTask(taskId, taskId); }
        }),
    );

    // Investigate from task tree
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.investigateFromTree', async (task: TaskItem) => {
            await investigateTask(task.external_id, task.title);
        }),
    );

    // Investigate by ID (for re-run)
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.investigateFromId', async (taskId: string) => {
            const cleanId = (taskId || '').replace(/^ado-/, '');
            await investigateTask(cleanId, cleanId);
        }),
    );

    // View report from tree
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.viewReportFromTree', async (inv: InvestigationSummary) => {
            await panelManager.openSavedReport(inv.id);
        }),
    );

    // View report from command palette
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.viewReport', async () => {
            try {
                const investigations = await apiService.listInvestigations();
                if (investigations.length === 0) {
                    vscode.window.showInformationMessage('No investigations found.');
                    return;
                }
                const items = investigations.map(inv => ({
                    label: inv.task_title || 'Unknown',
                    description: `${inv.status} \u00b7 ${inv.started_at?.substring(0, 10) || ''}`,
                    id: inv.id,
                }));
                const selected = await vscode.window.showQuickPick(items, { placeHolder: 'Select investigation' });
                if (selected) { await panelManager.openSavedReport(selected.id); }
            } catch (error) {
                vscode.window.showErrorMessage(`Failed: ${error}`);
            }
        }),
    );

    // Delete single investigation
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.deleteInvestigation', async (item: any) => {
            const inv = item?.investigation || item;
            if (!inv?.id) { return; }
            const confirm = await vscode.window.showWarningMessage(
                `Delete investigation "${inv.task_title || inv.id}"?`,
                { modal: true }, 'Delete',
            );
            if (confirm === 'Delete') {
                try {
                    await apiService.deleteInvestigation(inv.id);
                    vscode.window.showInformationMessage('Investigation deleted.');
                    await investigationTreeProvider.loadInvestigations();
                } catch (error) {
                    vscode.window.showErrorMessage(`Failed to delete: ${error}`);
                }
            }
        }),
    );

    // Delete all investigation history
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.deleteInvestigationHistory', async () => {
            const confirm = await vscode.window.showWarningMessage(
                'Delete all investigation history? This cannot be undone.',
                { modal: true }, 'Delete All',
            );
            if (confirm === 'Delete All') {
                try {
                    const result = await apiService.deleteAllInvestigations();
                    vscode.window.showInformationMessage(`Deleted ${result.deleted} investigation(s).`);
                    await investigationTreeProvider.loadInvestigations();
                } catch (error) {
                    vscode.window.showErrorMessage(`Failed: ${error}`);
                }
            }
        }),
    );

    // Re-run investigation
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.rerunInvestigation', async (item: any) => {
            const inv = item?.investigation || item;
            if (!inv?.task_id) { return; }
            const cleanId = inv.task_id.replace(/^ado-/, '');
            await investigateTask(cleanId, inv.task_title || cleanId);
        }),
    );

    // Cancel investigation (placeholder -- cancel is via notification bar)
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.cancelInvestigation', async (item: any) => {
            const inv = item?.investigation || item;
            if (!inv?.id) { return; }
            try {
                await apiService.cancelInvestigation(inv.id);
                vscode.window.showInformationMessage('Investigation cancelled.');
                await investigationTreeProvider.loadInvestigations();
            } catch (error) {
                vscode.window.showErrorMessage(`Failed to cancel: ${error}`);
            }
        }),
    );

    // History refresh
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.history', () => investigationTreeProvider.loadInvestigations()),
    );

    // Show status
    context.subscriptions.push(
        vscode.commands.registerCommand('traceai.showStatus', async () => {
            try {
                const status = await apiService.getStatus();
                vscode.window.showInformationMessage(
                    `TraceAI v${status.version} | ${status.configured ? 'Configured' : 'Not configured'} | ${status.ticket_source || 'No source'}`,
                    { modal: true },
                );
            } catch {
                vscode.window.showWarningMessage('TraceAI server is not running.', 'Start Server').then(a => {
                    if (a) { mainFlow(); }
                });
            }
        }),
        vscode.commands.registerCommand('traceai.status', () => vscode.commands.executeCommand('traceai.showStatus')),
    );

    // ── Startup ──────────────────────────────────────────────────────────

    mainFlow();

    // Background refresh
    refreshInterval = setInterval(() => { refreshTasks().catch(() => {}); }, 5 * 60 * 1000);
    context.subscriptions.push({ dispose: () => { if (refreshInterval) { clearInterval(refreshInterval); } } });
}

// ── Main Flow ────────────────────────────────────────────────────────────────

async function mainFlow(): Promise<void> {
    statusBarItem.text = '$(loading~spin) TraceAI: Starting backend...';

    const serverRunning = await serverManager.ensureRunning();
    if (!serverRunning) {
        statusBarItem.text = '$(error) TraceAI: Server offline';
        return;
    }
    statusBarItem.text = '$(check) TraceAI: Connected';

    try {
        const status = await apiService.getStatus();
        if (!status.configured) {
            statusBarItem.text = '$(gear) TraceAI: Setup required';
            const action = await vscode.window.showInformationMessage(
                'Welcome to TraceAI! Run the setup wizard to connect your ticket source and AI key.',
                'Run Setup', 'Later',
            );
            if (action === 'Run Setup') { vscode.commands.executeCommand('traceai.setup'); }
            return;
        }
    } catch {
        statusBarItem.text = '$(error) TraceAI: Connection failed';
        return;
    }

    statusBarItem.text = '$(loading~spin) TraceAI: Loading tasks...';
    const cached = await taskCache.loadCached();
    if (cached.length > 0) {
        taskTreeProvider.setTasks(cached);
        statusBarItem.text = `$(check) TraceAI: ${cached.length} tasks (cached)`;
    }

    await refreshTasks();
    try { await investigationTreeProvider.loadInvestigations(); } catch {}
    stateManager.markFirstRunComplete();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

async function refreshTasks(): Promise<void> {
    const config = vscode.workspace.getConfiguration('traceai');
    const assignee = stateManager.getAssignee() || config.get<string>('defaultAssignee', '');
    try {
        const tasks = await apiService.fetchTasks(assignee || undefined, undefined, 50, ['new', 'active', 'in_progress', 'unknown']);
        taskTreeProvider.setTasks(tasks);
        await taskCache.save(tasks);
        statusBarItem.text = `$(check) TraceAI: ${tasks.length} tasks`;
    } catch {
        const cached = await taskCache.loadCached();
        if (cached.length === 0) { statusBarItem.text = '$(warning) TraceAI: Fetch failed'; }
    }
}

async function investigateTask(taskId: string, taskTitle: string): Promise<void> {
    // Open a dedicated panel for this investigation
    const panel = panelManager.openProgress(taskId, taskTitle);

    let cancelled = false;

    try {
        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: `Investigating ${taskId}`, cancellable: true },
            async (progress, token) => {
                token.onCancellationRequested(() => {
                    cancelled = true;
                    panelManager.updateProgress(taskId, 'cancelled', 'Investigation cancelled.', 'cancelled');
                });

                progress.report({ message: 'Starting investigation...', increment: 5 });

                try {
                    // Use SSE streaming for real progress, with fallback to blocking POST
                    await new Promise<void>((resolve, reject) => {
                        apiService.investigateWithProgress(
                            taskId,
                            // onProgress — real backend progress events
                            (stage: string, message: string) => {
                                if (cancelled) { return; }
                                const progressMap: Record<string, number> = {
                                    'loading_ticket': 5, 'classifying': 8,
                                    'initializing_workspace': 10,
                                    'parallel_analysis': 15, 'parallel_execution': 20,
                                    'deep_investigation': 35, 'sql_intelligence': 50,
                                    'sql_queries': 50,
                                    'evidence_aggregation': 60, 'building_graph': 65,
                                    'graph_build': 65,
                                    'building_context': 70, 'ai_reasoning': 80,
                                    'generating_report': 90, 'report_generation': 90,
                                };
                                const pct = progressMap[stage] || 50;
                                progress.report({ message, increment: 5 });
                                panelManager.updateProgress(taskId, stage, message);
                            },
                            // onComplete — final report received
                            (report) => {
                                if (cancelled) { resolve(); return; }

                                panelManager.updateProgress(taskId, 'generating_report', 'Generating report...');
                                setTimeout(() => {
                                    panelManager.updateProgress(taskId, 'complete', 'Investigation complete!', 'complete');
                                    setTimeout(() => {
                                        panelManager.showReport(taskId, report);
                                        if (report.status === 'completed') {
                                            vscode.window.showInformationMessage(
                                                `Investigation complete: ${report.findings?.length || 0} finding(s)`
                                            );
                                        }
                                        resolve();
                                    }, 400);
                                }, 300);
                            },
                            // onError — investigation failed
                            (error: string) => {
                                if (!cancelled) {
                                    panelManager.updateProgress(taskId, 'error', `Failed: ${error}`, 'error');
                                    vscode.window.showErrorMessage(`Investigation failed: ${error}`);
                                }
                                resolve(); // Don't reject — we handled the error
                            },
                        ).catch(reject);
                    });
                } catch (err) {
                    if (!cancelled) {
                        panelManager.updateProgress(taskId, 'error', `Failed: ${err}`, 'error');
                        vscode.window.showErrorMessage(`Investigation failed: ${err}`);
                    }
                }
            },
        );

        if (!cancelled) { await investigationTreeProvider.loadInvestigations(); }
    } catch (error) {
        if (!cancelled) { vscode.window.showErrorMessage(`Investigation failed: ${error}`); }
    }
}

export function deactivate(): void {
    if (refreshInterval) { clearInterval(refreshInterval); }
    serverManager?.dispose();
}
