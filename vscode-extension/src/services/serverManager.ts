/**
 * TraceAI Server Manager — Auto-bootstrap and start the Python backend.
 *
 * For internal team distribution: teammates install the .vsix and everything
 * works automatically. No repo clone or manual setup required.
 *
 * Lifecycle:
 *   1. Check if server is already running (GET /api/health)
 *   2. Detect Python 3.11+ (python / python3)
 *   3. Create virtual environment at ~/.traceai/runtime/venv
 *   4. Install backend from bundled extension path
 *   5. Start server: venv/python -m task_analyzer.api.server
 *   6. Poll /api/health every 1s, timeout 30s
 *   7. Crash recovery: up to 3 auto-restart attempts
 */

import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import axios from 'axios';

const RUNTIME_DIR = path.join(os.homedir(), '.traceai', 'runtime');
const VENV_DIR = path.join(RUNTIME_DIR, 'venv');
const IS_WIN = process.platform === 'win32';
const VENV_PYTHON = IS_WIN
    ? path.join(VENV_DIR, 'Scripts', 'python.exe')
    : path.join(VENV_DIR, 'bin', 'python');
const VENV_PIP = IS_WIN
    ? path.join(VENV_DIR, 'Scripts', 'pip.exe')
    : path.join(VENV_DIR, 'bin', 'pip');

export class ServerManager {
    private serverProcess: cp.ChildProcess | undefined;
    private restartCount = 0;
    private readonly MAX_RESTARTS = 3;
    private readonly port: number;
    private readonly baseUrl: string;
    private readonly extensionPath: string;
    private disposed = false;
    private outputChannel: vscode.OutputChannel;

    constructor(port: number = 7420, extensionPath: string = '') {
        this.port = port;
        this.baseUrl = `http://127.0.0.1:${port}`;
        this.extensionPath = extensionPath;
        this.outputChannel = vscode.window.createOutputChannel('TraceAI Server');
    }

    /**
     * Ensure the server is running. Bootstraps the full environment if needed.
     * Returns true if server is alive after this call.
     */
    async ensureRunning(): Promise<boolean> {
        // 1. Check if already running (retry a few times for slow startup)
        for (let i = 0; i < 3; i++) {
            if (await this.isAlive()) {
                this.log('Server already running.');
                return true;
            }
            if (i < 2) { await new Promise(r => setTimeout(r, 2000)); }
        }

        // 2. Detect Python
        const pythonCmd = await this.detectPython();
        if (!pythonCmd) {
            vscode.window.showErrorMessage(
                'TraceAI requires Python 3.11+. Please install Python and reload VS Code.',
                'Download Python',
            ).then(action => {
                if (action === 'Download Python') {
                    vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
                }
            });
            return false;
        }
        this.log(`Python detected: ${pythonCmd}`);

        // 3. Create runtime directory
        this.ensureDir(RUNTIME_DIR);

        // 4. Create virtual environment if missing
        if (!fs.existsSync(VENV_PYTHON)) {
            this.log('Creating virtual environment...');
            const venvOk = await this.runCommand(pythonCmd, ['-m', 'venv', VENV_DIR]);
            if (!venvOk) {
                vscode.window.showErrorMessage(
                    'TraceAI: Failed to create Python virtual environment.',
                );
                return false;
            }
            this.log('Virtual environment created.');
        } else {
            this.log('Virtual environment exists.');
        }

        // 5. Install backend dependencies (skip if server came alive during setup)
        if (await this.isAlive()) {
            this.log('Server came alive during setup — skipping install.');
            return true;
        }
        const installed = await this.ensureBackendInstalled();
        if (!installed) {
            vscode.window.showErrorMessage(
                'TraceAI: Failed to install backend dependencies. Check the TraceAI Server output channel.',
            );
            return false;
        }

        // 6. Start the server
        return this.startServer();
    }

    /**
     * Check if the server is responding to health checks.
     */
    async isAlive(): Promise<boolean> {
        try {
            const resp = await axios.get(`${this.baseUrl}/api/health`, {
                timeout: 3000,
            });
            return resp.data?.status === 'ok';
        } catch {
            return false;
        }
    }

    /**
     * Detect a usable Python 3.11+ interpreter.
     * Tries 'python' then 'python3'.
     */
    private async detectPython(): Promise<string | null> {
        for (const cmd of ['python', 'python3']) {
            try {
                const version = await this.getCommandOutput(cmd, ['--version']);
                if (version) {
                    const match = version.match(/Python (\d+)\.(\d+)/);
                    if (match) {
                        const major = parseInt(match[1], 10);
                        const minor = parseInt(match[2], 10);
                        if (major >= 3 && minor >= 11) {
                            return cmd;
                        }
                    }
                }
            } catch {
                // Command not found, try next
            }
        }
        return null;
    }

    /**
     * Ensure the TraceAI backend is installed in the venv.
     * Installs from the bundled backend path inside the extension.
     */
    private async ensureBackendInstalled(): Promise<boolean> {
        // Check if already installed
        const checkOk = await this.getCommandOutput(VENV_PYTHON, [
            '-c', 'import task_analyzer; print(task_analyzer.__version__)',
        ]);
        if (checkOk && checkOk.trim()) {
            this.log(`Backend already installed: v${checkOk.trim()}`);
            return true;
        }

        // Determine the backend source path.
        // When packaged in .vsix, the backend is bundled at <extensionPath>/backend/
        // When developing locally, it's at the repo root (parent of vscode-extension/).
        let backendPath = path.join(this.extensionPath, 'backend');
        if (!fs.existsSync(path.join(backendPath, 'pyproject.toml'))) {
            // Fallback: development layout — repo root is parent of vscode-extension/
            backendPath = path.resolve(this.extensionPath, '..');
        }
        if (!fs.existsSync(path.join(backendPath, 'pyproject.toml'))) {
            this.log(`ERROR: Cannot find pyproject.toml. Tried: ${backendPath}`);
            return false;
        }

        this.log(`Installing backend from: ${backendPath}`);

        // Upgrade pip first (suppress errors)
        await this.runCommand(VENV_PIP, ['install', '--upgrade', 'pip', '--quiet']);

        // Install the backend package in editable mode
        const installOk = await this.runCommand(VENV_PIP, [
            'install', '-e', backendPath, '--quiet',
        ]);
        if (!installOk) {
            // Retry without editable mode (works better with bundled packages)
            this.log('Editable install failed, trying standard install...');
            const retryOk = await this.runCommand(VENV_PIP, [
                'install', backendPath, '--quiet',
            ]);
            if (!retryOk) {
                return false;
            }
        }

        this.log('Backend installed successfully.');
        return true;
    }

    /**
     * Start the Python backend server from the venv.
     */
    private async startServer(): Promise<boolean> {
        if (this.disposed) {
            return false;
        }

        this.log('Starting TraceAI server...');

        try {
            this.serverProcess = cp.spawn(
                VENV_PYTHON,
                ['-m', 'task_analyzer.api.server'],
                {
                    stdio: 'pipe',
                    env: { ...process.env },
                    cwd: RUNTIME_DIR,
                },
            );

            // Setup crash recovery
            this.setupCrashRecovery();

            // Capture stdout/stderr for diagnostics
            this.serverProcess.stdout?.on('data', (data: Buffer) => {
                this.log(data.toString().trim());
            });
            this.serverProcess.stderr?.on('data', (data: Buffer) => {
                const msg = data.toString().trim();
                if (msg) {
                    this.log(msg);
                }
            });

            // Poll /api/health every 1s, up to 30 attempts
            for (let i = 0; i < 30; i++) {
                await this.sleep(1000);
                if (await this.isAlive()) {
                    this.restartCount = 0;
                    this.log('Server started successfully.');
                    return true;
                }
            }

            this.log('Server did not respond within 30 seconds.');
            return false;
        } catch (error) {
            this.log(`Failed to spawn server: ${error}`);
            return false;
        }
    }

    /**
     * Setup crash recovery — auto-restart on unexpected exit.
     */
    private setupCrashRecovery(): void {
        if (!this.serverProcess) {
            return;
        }

        this.serverProcess.on('exit', async (code, signal) => {
            if (this.disposed) {
                return;
            }

            // Unexpected exit
            if (code !== 0 && code !== null) {
                this.log(`Server exited with code ${code} (signal: ${signal})`);

                if (this.restartCount < this.MAX_RESTARTS) {
                    this.restartCount++;
                    const msg = `TraceAI server exited unexpectedly (attempt ${this.restartCount}/${this.MAX_RESTARTS}). Restarting...`;
                    vscode.window.showWarningMessage(msg);
                    this.log(msg);
                    await this.startServer();
                } else {
                    const action = await vscode.window.showErrorMessage(
                        `TraceAI server failed to start after ${this.MAX_RESTARTS} attempts.`,
                        'Restart',
                        'Show Logs',
                    );
                    if (action === 'Restart') {
                        this.restartCount = 0;
                        await this.startServer();
                    } else if (action === 'Show Logs') {
                        this.outputChannel.show();
                    }
                }
            }
        });
    }

    /**
     * Get the path to the Python executable inside the venv.
     * Useful for the setup command in extension.ts.
     */
    getVenvPython(): string {
        return VENV_PYTHON;
    }

    /**
     * Dispose — kill server and clean up.
     */
    dispose(): void {
        this.disposed = true;
        if (this.serverProcess) {
            this.serverProcess.kill();
            this.serverProcess = undefined;
        }
    }

    // ── Private Helpers ──────────────────────────────────────────────────

    private log(message: string): void {
        const ts = new Date().toISOString().substring(11, 19);
        this.outputChannel.appendLine(`[${ts}] ${message}`);
    }

    private ensureDir(dirPath: string): void {
        if (!fs.existsSync(dirPath)) {
            fs.mkdirSync(dirPath, { recursive: true });
        }
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * Run a command and return true if it exits with code 0.
     */
    private runCommand(cmd: string, args: string[]): Promise<boolean> {
        return new Promise(resolve => {
            this.log(`> ${cmd} ${args.join(' ')}`);
            const proc = cp.spawn(cmd, args, {
                stdio: 'pipe',
                shell: IS_WIN,
                env: { ...process.env },
            });

            let stderr = '';
            proc.stdout?.on('data', (data: Buffer) => {
                this.log(data.toString().trim());
            });
            proc.stderr?.on('data', (data: Buffer) => {
                stderr += data.toString();
            });

            proc.on('close', (code) => {
                if (code !== 0) {
                    this.log(`Command failed (exit ${code}): ${stderr.trim()}`);
                }
                resolve(code === 0);
            });

            proc.on('error', (err) => {
                this.log(`Command error: ${err.message}`);
                resolve(false);
            });
        });
    }

    /**
     * Run a command and return its stdout.
     */
    private getCommandOutput(cmd: string, args: string[]): Promise<string | null> {
        return new Promise(resolve => {
            const proc = cp.spawn(cmd, args, {
                stdio: 'pipe',
                shell: IS_WIN,
                env: { ...process.env },
            });

            let stdout = '';
            proc.stdout?.on('data', (data: Buffer) => {
                stdout += data.toString();
            });

            proc.on('close', (code) => {
                resolve(code === 0 ? stdout.trim() : null);
            });

            proc.on('error', () => {
                resolve(null);
            });
        });
    }
}
