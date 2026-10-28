const MARKETPLACE_SUFFIXES = ["taobao.com", "tmall.com", "jd.com"];

export function isSupportedMarketplaceUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "https:" || url.protocol === "http:") &&
      MARKETPLACE_SUFFIXES.some((suffix) => url.hostname === suffix || url.hostname.endsWith(`.${suffix}`))
    );
  } catch {
    return false;
  }
}

export function isSupportedProductUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const path = url.pathname.toLowerCase();

    if (
      (host === "item.taobao.com" || host.endsWith(".tmall.com")) &&
      path.endsWith("/item.htm")
    ) {
      return Boolean(url.searchParams.get("id") || url.searchParams.get("itemId"));
    }

    if (host === "item.jd.com") return /^\/\d+\.html$/.test(path);
    if (host === "item.m.jd.com") {
      return /^\/product\/\d+\.html$/.test(path) || Boolean(url.searchParams.get("wareId"));
    }
    return false;
  } catch {
    return false;
  }
}
