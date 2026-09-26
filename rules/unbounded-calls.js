// Unit test fixtures for JS/TS unbounded-calls rules

import axios from 'axios';
import got from 'got';
import child_process, { exec, execFile, spawnSync } from 'child_process';
import cp from 'node:child_process';
import http from 'http';
import https from 'node:https';

// ============================================================================
// fetch-no-signal
// ============================================================================

// ruleid: fetch-no-signal
fetch("https://api.example.com/data");

// ruleid: fetch-no-signal
fetch("https://api.example.com/data", { method: "POST", headers: { "Content-Type": "application/json" } });

// ruleid: fetch-no-signal
window.fetch("https://api.example.com/data");

// ruleid: fetch-no-signal
globalThis.fetch("https://api.example.com/data", { method: "GET" });

// ok: fetch-no-signal
fetch("https://api.example.com/data", { signal: AbortSignal.timeout(5000) });

const controller = new AbortController();
// ok: fetch-no-signal
fetch("https://api.example.com/data", { signal: controller.signal });

const signal = AbortSignal.timeout(3000);
// ok: fetch-no-signal
fetch("https://api.example.com/data", { method: "GET", signal });

// ok: fetch-no-signal
window.fetch("https://api.example.com/data", { signal: AbortSignal.timeout(5000) });

// ok: fetch-no-signal
globalThis.fetch("https://api.example.com/data", { signal: controller.signal });

// Wrapper function passing options
// ok: fetch-no-signal
function customFetchWrapper(url, init) {
  return fetch(url, init);
}

// ok: fetch-no-signal
// nosemgrep: fetch-no-signal
fetch("https://api.example.com/unbounded-poll-with-watchdog");


// ============================================================================
// axios-no-timeout
// ============================================================================

// ruleid: axios-no-timeout
axios("https://api.example.com/data");

// ruleid: axios-no-timeout
axios("https://api.example.com/data", { method: "POST" });

// ruleid: axios-no-timeout
axios({ url: "https://api.example.com/data", method: "GET" });

// ruleid: axios-no-timeout
axios.get("https://api.example.com/data");

// ruleid: axios-no-timeout
axios.get("https://api.example.com/data", { headers: { Accept: "application/json" } });

// ruleid: axios-no-timeout
axios.post("https://api.example.com/data", { key: "value" });

// ruleid: axios-no-timeout
axios.post("https://api.example.com/data", { key: "value" }, { headers: {} });

// ruleid: axios-no-timeout
axios.delete("https://api.example.com/data/1");

// ruleid: axios-no-timeout
axios.create();

// ruleid: axios-no-timeout
axios.create({ baseURL: "https://api.example.com" });

// ok: axios-no-timeout
axios("https://api.example.com/data", { timeout: 5000 });

// ok: axios-no-timeout
axios.get("https://api.example.com/data", { timeout: 5000 });

// ok: axios-no-timeout
axios.get("https://api.example.com/data", { signal: AbortSignal.timeout(5000) });

// ok: axios-no-timeout
axios.post("https://api.example.com/data", { key: "value" }, { timeout: 10000 });

// ok: axios-no-timeout
const axiosClient = axios.create({ baseURL: "https://api.example.com", timeout: 5000 });
// Configured client instance
// ok: axios-no-timeout
axiosClient.get("/users");

// ok: axios-no-timeout
function axiosWrapper(url, options) {
  return axios.get(url, { ...options, timeout: 5000 });
}


// ============================================================================
// got-no-timeout
// ============================================================================

// ruleid: got-no-timeout
got("https://api.example.com/data");

// ruleid: got-no-timeout
got("https://api.example.com/data", { headers: {} });

// ruleid: got-no-timeout
got.get("https://api.example.com/data");

// ruleid: got-no-timeout
got.get("https://api.example.com/data", { searchParams: { page: 1 } });

// ruleid: got-no-timeout
got.post("https://api.example.com/data", { json: { item: 1 } });

// ruleid: got-no-timeout
got.extend({ prefixUrl: "https://api.example.com" });

// ok: got-no-timeout
got("https://api.example.com/data", { timeout: 5000 });

// ok: got-no-timeout
got("https://api.example.com/data", { timeout: { request: 5000 } });

// ok: got-no-timeout
got("https://api.example.com/data", { signal: AbortSignal.timeout(5000) });

// ok: got-no-timeout
got.get("https://api.example.com/data", { timeout: 5000 });

// ok: got-no-timeout
const gotClient = got.extend({ prefixUrl: "https://api.example.com", timeout: 5000 });
// Configured client instance
// ok: got-no-timeout
gotClient.get("items");


// ============================================================================
// child-process-no-timeout
// ============================================================================

// ruleid: child-process-no-timeout
child_process.exec("ls -la");

// ruleid: child-process-no-timeout
child_process.execFile("git", ["status"]);

// ruleid: child-process-no-timeout
child_process.spawnSync("git", ["status"]);

// ruleid: child-process-no-timeout
cp.exec("echo hi");

// ruleid: child-process-no-timeout
cp.spawnSync("npm", ["test"]);

// ruleid: child-process-no-timeout
exec("whoami");

// ruleid: child-process-no-timeout
execFile("node", ["script.js"]);

// ruleid: child-process-no-timeout
spawnSync("docker", ["ps"]);

// ok: child-process-no-timeout
child_process.exec("ls -la", { timeout: 5000 });

// ok: child-process-no-timeout
child_process.execFile("git", ["status"], { timeout: 5000 });

// ok: child-process-no-timeout
child_process.spawnSync("git", ["status"], { timeout: 5000 });

// ok: child-process-no-timeout
cp.exec("echo hi", { timeout: 3000 });

// ok: child-process-no-timeout
execFile("node", ["script.js"], { signal: controller.signal });

const procTimeout = 10000;
// ok: child-process-no-timeout
spawnSync("docker", ["ps"], { timeout: procTimeout });

// RegExp exec method
// ok: child-process-no-timeout
/test-pattern/.exec("test-pattern-string");


// ============================================================================
// node-http-no-timeout
// ============================================================================

// ruleid: node-http-no-timeout
http.get("http://example.com");

// ruleid: node-http-no-timeout
http.get("http://example.com", (res) => {});

// ruleid: node-http-no-timeout
http.request({ host: "example.com", path: "/" });

// ruleid: node-http-no-timeout
https.request("https://example.com", { method: "POST" });

// ruleid: node-http-no-timeout
https.get("https://example.com");

// ok: node-http-no-timeout
http.get("http://example.com", { timeout: 5000 });

// ok: node-http-no-timeout
http.get("http://example.com", { signal: AbortSignal.timeout(5000) });

// ok: node-http-no-timeout
http.request({ host: "example.com", timeout: 5000 });

// ok: node-http-no-timeout
https.request("https://example.com", { method: "POST", timeout: 5000 });

// ok: node-http-no-timeout
https.get("https://example.com", { timeout: 5000 }, (res) => {});
