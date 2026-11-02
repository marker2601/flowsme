// Renders one page to a 1920x1080 PNG, transparent where the page has no background.
//   node still.js <url> <out.png>
const puppeteer = require("puppeteer-core");

(async () => {
  const [url, out] = process.argv.slice(2);
  const browser = await puppeteer.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
                                           headless: true, args: ["--force-color-profile=srgb"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: "networkidle0" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: out, omitBackground: true });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
