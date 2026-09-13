// 临时调试脚本：在 Polymarket 页面控制台运行看 __NEXT_DATA__ 结构
const script = document.getElementById("__NEXT_DATA__");
if (script) {
  const data = JSON.parse(script.textContent || "{}");
  console.log("[KPAX DEBUG] __NEXT_DATA__ keys:", Object.keys(data));
  console.log("[KPAX DEBUG] props keys:", Object.keys(data.props || {}));
  console.log("[KPAX DEBUG] pageProps keys:", Object.keys(data.props?.pageProps || {}));

  // 递归搜索含 "outcomePrices" 的对象
  function findMarkets(obj: any, path = ""): void {
    if (!obj || typeof obj !== "object") return;
    if (obj.outcomePrices) {
      console.log(`[KPAX DEBUG] Found market at ${path}:`, {
        question: obj.question || obj.groupItemTitle || obj.title,
        outcomes: obj.outcomes,
        prices: obj.outcomePrices,
      });
    }
    for (const [key, val] of Object.entries(obj)) {
      if (typeof val === "object") findMarkets(val, `${path}.${key}`);
    }
  }
  findMarkets(data);
} else {
  console.log("[KPAX DEBUG] No __NEXT_DATA__ found");
}
export {};
