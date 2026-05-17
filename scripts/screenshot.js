// Capture a 1200x900 viewport screenshot of a generated outbox.cafe page.
// Usage: node scripts/screenshot.js <input.html> <output.png>

const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

(async () => {
  const [, , inFile, outFile] = process.argv;
  if (!inFile || !outFile) {
    console.error('usage: node screenshot.js <input.html> <output.png>');
    process.exit(1);
  }
  const absIn = path.resolve(inFile);
  if (!fs.existsSync(absIn)) {
    console.error('input not found:', absIn);
    process.exit(2);
  }
  fs.mkdirSync(path.dirname(outFile), { recursive: true });

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1200, height: 900, deviceScaleFactor: 1 });
    await page.goto('file://' + absIn, {
      waitUntil: 'networkidle0',
      timeout: 15000,
    });
    // settle any CSS animations
    await new Promise(r => setTimeout(r, 600));
    await page.screenshot({
      path: outFile,
      clip: { x: 0, y: 0, width: 1200, height: 900 },
      type: 'png',
    });
    console.log('shot →', outFile);
  } finally {
    await browser.close();
  }
})().catch(err => {
  console.error(err.message || err);
  process.exit(3);
});
