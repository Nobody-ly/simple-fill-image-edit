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
for (const name of ["source", "reference", "mask", "prompt", "out-dir"]) {
  if (!args[name]) throw new Error(`--${name} is required`);
}

const outDir = path.resolve(args["out-dir"]);
await fs.mkdir(outDir, {recursive: true});
const [source, reference, mask, prompt] = await Promise.all([
  fs.readFile(args.source),
  fs.readFile(args.reference),
  fs.readFile(args.mask),
  fs.readFile(args.prompt, "utf8"),
]);

const response = await fetch("https://app.catsco.cc/v1/images/edits", {
  method: "POST",
  headers: {
    Authorization: auth(),
    "Content-Type": "application/json",
    "X-CatsCo-Image-Provider": "image2",
  },
  body: JSON.stringify({
    model: "gpt-image-2",
    prompt: prompt.trim(),
    images: [
      {image_url: dataUrl(source)},
      {image_url: dataUrl(reference)},
    ],
    mask: dataUrl(mask),
    n: 1,
    size: args.size || "1024x1024",
    quality: "high",
    output_format: "png",
  }),
});

const raw = await response.text();
let body;
try { body = JSON.parse(raw); } catch { body = null; }
if (!response.ok) {
  const message = body?.error?.message || body?.error || body?.message || raw.slice(0, 500);
  throw new Error(`CatsCo style-reference edit HTTP ${response.status}: ${message}`);
}
const encoded = body?.data?.[0]?.b64_json;
if (!encoded) throw new Error("CatsCo style-reference edit returned no image");
await fs.writeFile(path.join(outDir, "result.png"), Buffer.from(encoded, "base64"));
await fs.writeFile(path.join(outDir, "response.json"), JSON.stringify({
  provider: response.headers.get("x-catsco-image-provider") || null,
  request_id: response.headers.get("x-request-id") || null,
  created: body.created || null,
}, null, 2));
process.stdout.write(JSON.stringify({ok: true, out_dir: outDir}));
