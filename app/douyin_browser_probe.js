const { chromium } = require('playwright-core');
const fs = require('fs');

function resolveExecutablePath() {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_PATH,
    process.env.CHROME_BIN,
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  try {
    const bundled = chromium.executablePath();
    if (bundled && fs.existsSync(bundled)) return bundled;
  } catch {}
  throw new Error('No Chromium/Chrome executable found');
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function collectDomImages(page) {
  try {
    return await page.evaluate(() =>
      Array.from(document.querySelectorAll('img')).map((img) => ({
        src: img.getAttribute('src') || '',
        dataSrc: img.getAttribute('data-src') || '',
        w: img.naturalWidth || img.width || 0,
        h: img.naturalHeight || img.height || 0,
        alt: img.getAttribute('alt') || '',
      })),
    );
  } catch {
    return [];
  }
}

async function main() {
  const input = process.argv[2];
  if (!input) {
    console.error('missing url');
    process.exit(2);
  }

  const browser = await chromium.launch({
    executablePath: resolveExecutablePath(),
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext({
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 1200 },
  });
  const page = await context.newPage();

  const seen = [];
  page.on('response', async (resp) => {
    const url = resp.url();
    if (!/douyin|iesdouyin|zjcdn|douyinvod|douyinpic/.test(url)) return;
    const ct = resp.headers()['content-type'] || '';
    let text = '';
    if (ct.includes('json') || ct.includes('html') || ct.includes('text/plain')) {
      try {
        text = await resp.text();
      } catch {}
    }
    seen.push({ url, status: resp.status(), ct, len: text.length, text: text.slice(0, 200000) });
  });

  await page.goto(input, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(8000);

  try {
    await page.mouse.click(720, 420);
    await sleep(3000);
    await page.keyboard.press('Space');
    await sleep(3000);
  } catch {}

  try {
    await page.evaluate(() => {
      const vids = Array.from(document.querySelectorAll('video'));
      for (const v of vids) {
        try {
          v.muted = true;
          v.play().catch(() => {});
        } catch {}
      }
    });
  } catch {}

  await sleep(12000);

  const mediaResponses = seen.filter((x) => /douyinvod\.com|zjcdn\.com|mime_type=video_mp4|\/video\/tos\//.test(x.url));
  const domImages = await collectDomImages(page);
  const html = await page.content();
  const out = {
    input,
    finalUrl: page.url(),
    title: await page.title(),
    html: html.slice(0, 220000),
    seen,
    mediaResponses,
    domImages,
    videoInfo: await page.evaluate(() =>
      Array.from(document.querySelectorAll('video')).map((v) => ({
        src: v.currentSrc || v.src || '',
        paused: v.paused,
        muted: v.muted,
        readyState: v.readyState,
        networkState: v.networkState,
        error: v.error ? { code: v.error.code, message: v.error.message || '' } : null,
      })),
    ),
  };
  fs.writeFileSync('/tmp/douyin-browser-out.json', JSON.stringify(out, null, 2));
  console.log(
    JSON.stringify(
      {
        ok: true,
        finalUrl: out.finalUrl,
        title: out.title,
        mediaResponseCount: mediaResponses.length,
        domImageCount: domImages.length,
      },
      null,
      2,
    ),
  );
  await browser.close();
}

main().catch((err) => {
  console.error((err && err.stack) || String(err));
  process.exit(1);
});
