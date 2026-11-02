// DCR PAIS for P4: runs the TO-BE DCR graph with processdoctor's engine (dcr-js).
// CEP -> PAIS: POST /research-output executes "Update research output".
// PAIS -> CEP: every executed event is sent to Siddhi.
import fs from "node:fs";
import http from "node:http";
import { Moddle } from "moddle";
import { Reader } from "moddle-xml";
import { moddleToDCR } from "./vendor/dcr-js/dcr-engine/src/graphConversion.ts";
import { executeS, isEnabledS } from "./vendor/dcr-js/dcr-engine/src/executionEngine.ts";

const SIDDHI = "http://localhost:8006/dcr-events";
const DAY = 86_400_000;

// Load the model
const res = "./vendor/dcr-js/modeler/lib/moddle/resources/";
const pkgs: any = {};
for (const f of fs.readdirSync(res)) {
    const j = JSON.parse(fs.readFileSync(res + f, "utf8"));
    pkgs[j.prefix] = j;
}
const xml = fs.readFileSync("p4_career_advancement_tobe.xml", "utf8");
const model: any = await new Reader(new Moddle(pkgs)).fromXML(xml, "dcr:Definitions");
const els: any[] = [];
const wrap = (bo: any): any => {
    const kids = bo.boardElements || [];
    const el = {
        id: bo.id,
        type: bo.$type,
        businessObject: bo,
        children: kids.filter((c: any) => c.$type !== "dcr:Relation").map(wrap),
    };
    els.push(el);
    kids.filter((c: any) => c.$type === "dcr:Relation").forEach((r: any) =>
        els.push({ id: r.id, type: r.$type, businessObject: r }),
    );
    return el;
};
wrap(model.rootElement.rootElements[0]);
const registry = { filter: (f: any) => els.filter(f), get: (id: string) => els.find((e) => e.id === id) };
const role: Record<string, string> = Object.fromEntries(
    els.filter((e) => e.type === "dcr:Event").map((e) => [e.id, e.businessObject.role]),
);

// One case = one tenure-track employee
const cases: Record<string, { authorId: string; g: any; now: Date; log: any[] }> = {};

async function execute(id: string, ev: string) {
    const c = cases[id];
    const st = isEnabledS(ev, c.g, c.g.subProcessMap[ev] ?? c.g, {}, c.now);
    if (!st.enabled) return st;
    executeS(ev, c.g, {}, c.now);
    const label = c.g.labelMap[ev];
    c.log.push({ label, role: role[ev], time: c.now.toISOString() });
    console.log(`[${id}] ${c.now.toISOString().slice(0, 10)} ${label}`);
    fetch(SIDDHI, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event: { caseId: id, authorId: c.authorId, activity: ev, label } }),
    }).catch(() => console.log("Siddhi not reachable"));
    await autoRun(id);
    return st;
}

// The case system executes its own timer and check events when they are due
async function autoRun(id: string) {
    const c = cases[id];
    for (const e of c.g.events)
        if (
            role[e] === "Case system" &&
            e !== "update" &&
            c.g.marking.included.has(e) &&
            c.g.marking.pending.has(e) &&
            isEnabledS(e, c.g, c.g.subProcessMap[e] ?? c.g, {}, c.now).enabled
        )
            return execute(id, e);
}

function xes() {
    const ev = (l: any) =>
        `<event><string key="concept:name" value="${l.label}"/><string key="org:resource" value="${l.role}"/>` +
        `<date key="time:timestamp" value="${l.time}"/></event>`;
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<log xes.version="1.0" xmlns="http://www.xes-standard.org/">\n' +
        Object.entries(cases)
            .map(
                ([id, c]) => `<trace><string key="concept:name" value="${id}"/>\n${c.log.map(ev).join("\n")}\n</trace>`,
            )
            .join("\n") +
        "\n</log>\n"
    );
}

// REST API
http.createServer(async (req, res) => {
    let body = "";
    for await (const d of req) body += d;
    const b = body ? (JSON.parse(body).event ?? JSON.parse(body)) : {};
    const [a, id, kind, ev] = (req.url ?? "").split("/").filter(Boolean);
    const reply = (code: number, data: any) => {
        res.writeHead(code);
        res.end(typeof data === "string" ? data : JSON.stringify(data) + "\n");
    };

    if (a === "cases" && !id) {
        // POST /cases {caseId, authorId, start}
        cases[b.caseId] = { authorId: b.authorId, g: moddleToDCR(registry), now: new Date(b.start), log: [] };
        return reply(201, { created: b.caseId });
    }
    if (a === "research-output") {
        // from Siddhi
        for (const [cid, c] of Object.entries(cases)) if (c.authorId === b.authorId) await execute(cid, "update");
        return reply(200, { ok: true });
    }
    if (a === "log.xes") return reply(200, xes()); // event log for processdoctor
    if (a === "cases" && cases[id] && kind === "events") {
        // POST /cases/{id}/events/{event}
        const st = await execute(id, ev);
        return st.enabled ? reply(200, { executed: ev }) : reply(409, { blocked: st.msg });
    }
    if (a === "cases" && cases[id] && kind === "advance") {
        // POST /cases/{id}/advance {days}
        for (let i = 0; i < b.days; i++) {
            cases[id].now = new Date(cases[id].now.getTime() + DAY);
            await autoRun(id);
        }
        return reply(200, { now: cases[id].now });
    }
    reply(404, { error: "unknown" });
}).listen(8090, () => console.log("DCR PAIS on http://localhost:8090"));
