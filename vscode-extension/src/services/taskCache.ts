/**
 * TraceAI Task Cache — Local task cache at ~/.traceai/cache/tasks.json
 *
 * Provides instant task loading on startup by caching the last
 * fetched task list. Cache is considered stale after 5 minutes.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { TaskItem } from './apiService';

const CACHE_DIR = path.join(os.homedir(), '.traceai', 'cache');
const CACHE_FILE = path.join(CACHE_DIR, 'tasks.json');
const DEFAULT_MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes

interface CacheData {
    tasks: TaskItem[];
    timestamp: number;
}

export class TaskCache {
    /**
     * Load cached tasks from disk.
     */
    async loadCached(): Promise<TaskItem[]> {
        try {
            if (!fs.existsSync(CACHE_FILE)) {
                return [];
            }
            const raw = fs.readFileSync(CACHE_FILE, 'utf-8');
            const data: CacheData = JSON.parse(raw);
            return data.tasks || [];
        } catch {
            return [];
        }
    }

    /**
     * Save tasks to the local cache.
     */
    async save(tasks: TaskItem[]): Promise<void> {
        try {
            // Ensure cache directory exists
            if (!fs.existsSync(CACHE_DIR)) {
                fs.mkdirSync(CACHE_DIR, { recursive: true });
            }
            const data: CacheData = {
                tasks,
                timestamp: Date.now(),
            };
            fs.writeFileSync(CACHE_FILE, JSON.stringify(data, null, 2), 'utf-8');
        } catch {
            // Silently fail — cache is best-effort
        }
    }

    /**
     * Check if the cache is stale (older than maxAgeMs).
     */
    isStale(maxAgeMs: number = DEFAULT_MAX_AGE_MS): boolean {
        try {
            if (!fs.existsSync(CACHE_FILE)) {
                return true;
            }
            const raw = fs.readFileSync(CACHE_FILE, 'utf-8');
            const data: CacheData = JSON.parse(raw);
            return (Date.now() - data.timestamp) > maxAgeMs;
        } catch {
            return true;
        }
    }
}
