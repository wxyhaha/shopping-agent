import { collectCurrentProductSnapshot } from "./capture-adapters";
export { inspectVariants, validateVariantTarget } from "./variant-controller";
// Shared with extension integration tests; the background collector keeps using collect().
export { captureOpenedProduct } from "./page-capture";

// This entry is bundled as trusted browser code for the Python collector.
// It has no extension API dependency and shares exactly the page extraction rules.
export function collect() {
  return collectCurrentProductSnapshot(document, location.href, document.title);
}
