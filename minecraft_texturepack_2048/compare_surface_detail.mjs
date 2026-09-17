// Render the actual viewer in a disposable Chrome profile, without dependencies.
// node compare_surface_detail.mjs BEFORE_PREFIX AFTER_PREFIX OUTPUT_DIRECTORY
// Both sides use vanilla_cobble_v2.png and the BEFORE specular map.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const [before, after, destination] = process.argv.slice(2);
if (!before || !after || !destination || ![before, after].every(s => /^[a-zA-Z0-9_-]+$/.test(s))) {
    throw new Error("Usage: node compare_surface_detail.mjs BEFORE_PREFIX AFTER_PREFIX OUTPUT_DIRECTORY");
}
const output = resolve(destination);
mkdirSync(output, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), "metallum-pbr-browser-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--hide-scrollbars", "--force-device-scale-factor=1", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
const closed = new Promise(resolve => chrome.on("close", resolve));
let socket;
const pending = new Map();
let nextId = 0;
function send(method, params = {}, sessionId) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timed out: ${method}`)); }, 30000);
        pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); },
            reject: error => { clearTimeout(timer); reject(error); } });
        socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
}
const errors = [];
try {
    const endpoint = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Chrome did not start")), 15000);
        let log = "";
        chrome.on("error", reject);
        chrome.stderr.on("data", data => {
            log += data;
            const match = log.match(/DevTools listening on (ws:\/\/[^\s]+)/);
            if (match) { clearTimeout(timer); resolve(match[1]); }
        });
    });
    socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    socket.onmessage = event => {
        const message = JSON.parse(event.data);
        if (message.id) {
            const request = pending.get(message.id);
            pending.delete(message.id);
            if (message.error) request?.reject(new Error(message.error.message));
            else request?.resolve(message.result);
        } else if (message.method === "Fetch.requestPaused") {
            instrument(message.params, message.sessionId).catch(error => errors.push(String(error)));
        } else if (message.method === "Runtime.exceptionThrown") {
            errors.push(JSON.stringify(message.params.exceptionDetails));
        } else if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
            errors.push(message.params.args.map(arg => arg.value || arg.description).join(" "));
        }
    };
    async function instrument(request, sessionId) {
        const body = await send("Fetch.getResponseBody", { requestId: request.requestId }, sessionId);
        let html = body.base64Encoded ? Buffer.from(body.body, "base64").toString() : body.body;
        // Test-only access exists only in this intercepted response, never the viewer on disk.
        html = html.replace('loadPreset("vanilla_cobble_mat");', `
window.__ready = loadPreset("vanilla_cobble_mat");
window.__compare = async (prefix, colored) => {
    await window.__ready;
    document.querySelectorAll(".panel, #hud, #togglePanel").forEach(el => el.style.display = "none");
    document.getElementById("chkAO").checked = false;
    document.getElementById("chkAlbedo").checked = colored;
    document.getElementById("dispScale").value = "0.06";
    document.getElementById("normalScale").value = "1";
    document.getElementById("chkOrbitSun").checked = false;
    document.getElementById("chkAutoRotate").checked = false;
    setGeometry("plane");
    currentMesh.geometry.dispose();
    currentMesh.geometry = new THREE.PlaneGeometry(1, 1, 256, 256);
    mapNormal = mapHeight = mapAO = await loadTexture(prefix + "_n.png", false);
    mapRoughness = await loadTexture("${before}_s.png", false);
    controls.enabled = false;
    camera.position.set(-0.22, -0.04, 0.45);
    controls.target.set(-0.22, 0.11, 0);
    camera.lookAt(controls.target);
    dirLight.position.set(-1, 1, 1);
    inspectMode = "pbr";
    updateMaterial();
    renderer.compile(scene, camera);
    renderer.render(scene, camera);
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    return { width: renderer.domElement.width, depth: material.displacementScale };
};
`);
        await send("Fetch.fulfillRequest", { requestId: request.requestId, responseCode: 200,
            responseHeaders: [{ name: "Content-Type", value: "text/html" }],
            body: Buffer.from(html).toString("base64") }, sessionId);
    }
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    const call = (method, params) => send(method, params, sessionId);
    const evaluate = async expression => {
        const result = await call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    };
    await call("Page.enable");
    await call("Runtime.enable");
    await call("Emulation.setDeviceMetricsOverride", { width: 900, height: 760, deviceScaleFactor: 1, mobile: false });
    await call("Fetch.enable", { patterns: [{ urlPattern: "*labpbr-viewer.html*", requestStage: "Response", resourceType: "Document" }] });
    await call("Page.navigate", { url: "http://127.0.0.1:8765/labpbr-viewer.html" });
    await evaluate(`new Promise((resolve, reject) => {
        const start = Date.now();
        const timer = setInterval(() => {
            if (window.__compare) { clearInterval(timer); resolve(true); }
            else if (Date.now() - start > 20000) { clearInterval(timer); reject(new Error("Viewer failed to load")); }
        }, 100);
    })`);
    const shots = [];
    for (const colored of [true, false]) {
        for (const [label, prefix] of [["Before", before], ["After", after]]) {
            await evaluate(`window.__compare(${JSON.stringify(prefix)}, ${colored})`);
            if (errors.length) throw new Error(errors.join("\n"));
            const { data } = await call("Page.captureScreenshot", { format: "png" });
            const filename = `${label.toLowerCase()}-${colored ? "color" : "relief"}.png`;
            writeFileSync(join(output, filename), Buffer.from(data, "base64"));
            shots.push({ label: `${label} · ${colored ? "same albedo" : "neutral surface"}`, data });
        }
    }
    const report = `<!doctype html><html lang="en"><meta charset="utf-8">
<title>Rock detail — matched comparison</title>
<style>
body{margin:0;padding:28px;background:#17191d;color:#eee;font:16px system-ui}
h1{font-size:26px;margin:0 0 8px}p{color:#c8cbd0;margin:0 0 20px}
main{display:grid;grid-template-columns:1fr 1fr;gap:16px}
figure{margin:0}figcaption{padding:8px 0;font-weight:600}img{width:100%;display:block}
</style>
<h1>Rock detail: before / after</h1>
<p>Same 1024² source, camera, lighting, roughness, 0.06 depth, and normal override 1. AO off. No upscale or added noise.</p>
<main>${shots.map(shot => `<figure><figcaption>${shot.label}</figcaption><img alt="${shot.label}" src="data:image/png;base64,${shot.data}"></figure>`).join("")}</main>
<p style="margin-top:16px">Before: ${before}. After: ${after}. Fine relief is inferred from source contrast, not measured geometry.</p></html>`;
    const reportPath = join(output, "comparison.html");
    writeFileSync(reportPath, report);
    await call("Fetch.disable");
    await call("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1500, deviceScaleFactor: 1, mobile: false });
    await call("Page.navigate", { url: pathToFileURL(reportPath).href });
    await evaluate(`new Promise(resolve => {
        const wait = () => document.images.length === 4
            ? Promise.all(Array.from(document.images, img => img.decode())).then(resolve)
            : setTimeout(wait, 50);
        wait();
    })`);
    const layout = await call("Page.getLayoutMetrics");
    const { data } = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: 1600, height: Math.ceil(layout.cssContentSize.height), scale: 1 } });
    writeFileSync(join(output, "comparison.png"), Buffer.from(data, "base64"));
    writeFileSync(join(output, "validation.json"), JSON.stringify({ before, after, errors, depth: 0.06,
        source: "vanilla_cobble_v2.png", specular: `${before}_s.png`, ao: false }, null, 2));
    console.log(`PASS: actual viewer rendered without JS/shader errors\n${reportPath}\n${join(output, "comparison.png")}`);
} finally {
    // Close only the disposable browser started by this script.
    if (socket?.readyState === WebSocket.OPEN) {
        await send("Browser.close").catch(() => {});
        socket.close();
    }
    if (chrome.exitCode === null) chrome.kill("SIGTERM");
    await closed;
    rmSync(profile, { recursive: true, force: true });
}
