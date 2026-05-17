const puppeteer = require("puppeteer");
(async () => {
  const browser = await puppeteer.launch({headless: "new", args: ["--no-sandbox"]});
  const page = await browser.newPage();
  await page.setViewport({width: 1400, height: 1000});
  await page.goto("https://outbox.cafe/archive", {waitUntil: "networkidle0", timeout: 20000});
  await new Promise(r => setTimeout(r, 1500));
  await page.screenshot({path: "/tmp/cabinet_live.png", fullPage: false});
  // also gather any console errors
  const failedImgs = await page.evaluate(() => {
    return Array.from(document.querySelectorAll("img.card-art-img")).map(i => ({
      src: i.src,
      naturalWidth: i.naturalWidth,
      complete: i.complete,
    }));
  });
  console.log(JSON.stringify(failedImgs, null, 2));
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
