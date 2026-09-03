import fs from "node:fs/promises";
import path from "node:path";

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error("invalid arguments");
    values[key.slice(2)] = value;
  }
  return values;
}

function dataUrl(bytes) {
  return `data:image/png;base64,${bytes.toString("base64")}`;
}

function auth() {
  const bot = String(process.env.CATSCO_API_KEY || process.env.CATSCOMPANY_API_KEY || "").trim();
  if (bot) return `ApiKey ${bot}`;
  const user = String(process.env.CATSCO_USER_TOKEN || process.env.CATSCOMPANY_USER_TOKEN || "").trim();
  if (user) return `Bearer ${user}`;
  throw new Error("CatsCo identity is unavailable");
}

const args = parseArgs(process.argv.slice(2));
for (const name of ["source", "mask", "prompt", "out-dir"]) {
  if (!args[name]) throw new Error(`--${name} is required`);
}
const outDir = path.resolve(args["out-dir"]);
const resultPath = path.join(outDir, "native-mask-result.png");
const metadataPath = path.join(outDir, "native-mask-response.json");
try {
  await fs.access(resultPath);
  await fs.access(metadataPath);
  process.stdout.write(JSON.stringify({ok: true, resumed: true, result_path: resultPath}));
  process.exit(0);
} catch {}

await fs.mkdir(outDir, {recursive: true});
const [source, mask, prompt] = await Promise.all([
  fs.readFile(args.source),
  fs.readFile(args.mask),
  fs.readFile(args.prompt, "utf8"),
]);
const requestRecord = {
  model: args.model || "gpt-image-2",
  prompt_sha256: null,
  source_bytes: source.length,
  mask_bytes: mask.length,
  size: args.size || "1024x1024",
  quality: args.quality || "high",
  output_format: "png",
  endpoint_path: "/v1/images/edits",
  transport: "catsco-json-to-provider-multipart-mask",
};
await fs.writeFile(path.join(outDir, "native-mask-request-record.json"), JSON.stringify(requestRecord, null, 2));

const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), Number(args.timeout || 600000));
let response;
try {
  response = await fetch("https://app.catsco.cc/v1/images/edits", {
    method: "POST",
    headers: {
      Authorization: auth(),
      "Content-Type": "application/json",
      "X-CatsCo-Image-Provider": "image2",
    },
    body: JSON.stringify({
      model: args.model || "gpt-image-2",
      prompt: prompt.trim(),
      images: [{image_url: dataUrl(source)}],
      mask: dataUrl(mask),
      n: 1,
      size: args.size || "1024x1024",
      quality: args.quality || "high",
      output_format: "png",
    }),
    signal: controller.signal,
  });
} finally {
  clearTimeout(timeout);
}
const text = await response.text();
let body;
try { body = JSON.parse(text); } catch { body = null; }
if (!response.ok) {
  const message = body?.error?.message || body?.error || body?.message || text.slice(0, 500);
  throw new Error(`CatsCo native mask gateway HTTP ${response.status}: ${message}`);
}
const encoded = body?.data?.[0]?.b64_json;
if (!encoded) throw new Error("CatsCo native mask gateway returned no b64_json image");
const bytes = Buffer.from(encoded, "base64");
if (bytes.length < 8 || bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") {
  throw new Error("CatsCo native mask gateway returned invalid PNG bytes");
}
await fs.writeFile(resultPath, bytes);
const metadata = {
  ok: true,
  result_path: resultPath,
  provider: response.headers.get("x-catsco-image-provider") || null,
  request_id: response.headers.get("x-request-id") || null,
  created: body.created || null,
  bytes: bytes.length,
  request: requestRecord,
};
await fs.writeFile(metadataPath, JSON.stringify(metadata, null, 2));
process.stdout.write(JSON.stringify(metadata));
