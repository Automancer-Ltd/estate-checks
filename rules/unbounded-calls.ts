// TypeScript-specific test fixtures for unbounded-calls rules

import axios, { type AxiosRequestConfig } from 'axios';
import got, { type OptionsOfDefaultResponseType } from 'got';
import child_process, { exec, execFile, spawnSync } from 'node:child_process';
import http from 'node:http';
import https from 'node:https';

// ============================================================================
// fetch-no-signal
// ============================================================================

// ruleid: fetch-no-signal
fetch("https://api.example.com/items");

// ruleid: fetch-no-signal
fetch("https://api.example.com/items", { method: "POST" });

// ok: fetch-no-signal
fetch("https://api.example.com/items", { signal: AbortSignal.timeout(5000) });

const tsController: AbortController = new AbortController();
// ok: fetch-no-signal
fetch("https://api.example.com/items", { signal: tsController.signal });

// Wrapper function adding timeout
// ok: fetch-no-signal
function tsFetchWrapper(url: string, init?: RequestInit): Promise<Response> {
  const signal = init?.signal ?? AbortSignal.timeout(5000);
  return fetch(url, { ...init, signal });
}


// ============================================================================
// axios-no-timeout
// ============================================================================

// ruleid: axios-no-timeout
axios.get<string>("https://api.example.com/data");

// ok: axios-no-timeout
axios.get<string>("https://api.example.com/data", { timeout: 5000 });

// Configured client instance
// ok: axios-no-timeout
const typedAxiosClient = axios.create({ timeout: 5000 });
typedAxiosClient.get("/items");


// ============================================================================
// got-no-timeout
// ============================================================================

// ruleid: got-no-timeout
got<string>("https://api.example.com/data");

// ok: got-no-timeout
got<string>("https://api.example.com/data", { timeout: 5000 });

// Configured client instance
// ok: got-no-timeout
const typedGotClient = got.extend({ timeout: 5000 });
typedGotClient.get("items");


// ============================================================================
// child-process-no-timeout
// ============================================================================

// ruleid: child-process-no-timeout
child_process.execFile("git", ["status"]);

// ok: child-process-no-timeout
child_process.execFile("git", ["status"], { timeout: 5000 });

// Wrapper function adding timeout
// ok: child-process-no-timeout
function runGit(args: string[]): string {
  const res = spawnSync("git", args, { timeout: 10000, encoding: "utf8" });
  return res.stdout;
}


// ============================================================================
// node-http-no-timeout
// ============================================================================

// ruleid: node-http-no-timeout
http.get("http://example.com");

// ok: node-http-no-timeout
http.get("http://example.com", { timeout: 5000 });
