// Link-checks the frontend module graph. ES module imports are resolved before
// any module body runs, so a missing export surfaces here even though the DOM
// is only stubbed well enough to get past evaluation.
const passthrough = new Proxy(function () {}, {
  get(_target, property) {
    if (property === Symbol.toPrimitive) return () => "";
    if (property === Symbol.iterator) return function* () {};
    if (property === "then") return undefined;
    if (property === "length") return 0;
    return passthrough;
  },
  apply: () => passthrough,
  construct: () => passthrough,
  set: () => true,
  has: () => true,
});

for (const [name, value] of [
  ["window", passthrough],
  ["document", passthrough],
  ["localStorage", passthrough],
  ["sessionStorage", passthrough],
  ["navigator", passthrough],
  ["location", passthrough],
  ["history", passthrough],
  ["matchMedia", () => passthrough],
  ["fetch", async () => passthrough],
  ["requestAnimationFrame", () => 0],
  ["cancelAnimationFrame", () => {}],
]) {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

const LINK_ERRORS = ["does not provide an export named", "Cannot find module", "Unexpected", "SyntaxError"];

const ENTRY = new URL("../static/app.js", import.meta.url);

try {
  await import(ENTRY);
  console.log("LINK OK: module graph resolved and evaluated cleanly");
} catch (error) {
  const message = String(error && error.message);
  const isLinkError =
    error instanceof SyntaxError || LINK_ERRORS.some((needle) => message.includes(needle));
  if (isLinkError) {
    console.log("LINK FAILED:", message);
    process.exitCode = 1;
  } else {
    console.log("LINK OK (evaluation stopped on a DOM stub, which is expected)");
    console.log("  stopped at:", error.constructor.name + ":", message.slice(0, 160));
  }
}
