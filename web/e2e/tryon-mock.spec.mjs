import assert from 'node:assert/strict';

const waitForLog = async (page, method, count = 1) => {
  await page.waitForFunction(([wanted, minimum]) =>
    (window.__DECART_MOCK_LOG__ || []).filter((entry) => entry.method === wanted).length >= minimum,
  [method, count], { timeout: 12_000 });
};

export async function run({ chromium, baseURL }) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ permissions: ['camera'] });
  await context.addInitScript(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 480; canvas.height = 720;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#d8c4b0'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#263238'; ctx.fillRect(150, 80, 180, 560);
    const stream = canvas.captureStream(15);
    navigator.mediaDevices.getUserMedia = async () => stream;
  });
  const page = await context.newPage();
  try {
    const seed = await page.request.post(`${baseURL}/api/wardrobe/seed-demo`);
    assert.equal(seed.ok(), true);
    const items = await (await page.request.get(`${baseURL}/api/wardrobe/items`)).json();
    const top = items.find((item) => item.category === 'top');
    const bottom = items.find((item) => item.category === 'bottom');
    assert.ok(top && bottom, 'seed must provide top and bottom garments');

    await page.goto(`${baseURL}/#/stylist`);
    await page.locator('.wd-chat-input').fill('Tôi đi đám cưới ở Hà Nội');
    await page.locator('.wd-chat-send').click();
    const handoff = page.getByRole('button', { name: 'Mặc thử trực tiếp' }).first();
    await handoff.waitFor();
    await handoff.click();
    assert.match(page.url(), /#\/tryon-live\?outfit=/);

    const outfit = encodeURIComponent(JSON.stringify([top.id, bottom.id]));
    await page.goto(`${baseURL}/#/tryon-live?mock=1&outfit=${outfit}`);
    await page.getByRole('button', { name: 'Bắt đầu mặc thử' }).click();
    await waitForLog(page, 'setImage');
    const cards = page.locator('.tl-card-item');
    if (await cards.count() > 1) {
      await cards.nth(1).click();
      await waitForLog(page, 'setImage', 2);
    }
    const log = await page.evaluate(() => window.__DECART_MOCK_LOG__);
    assert.equal(log.filter((entry) => entry.method === 'connect').length, 1, 'switch must not reconnect');
    assert.match(await page.locator('.tl-ai-note').innerText(), /AI|Decart|không chính xác/);

    await page.evaluate(() => { location.hash = '#/home'; });
    await waitForLog(page, 'disconnect');
    assert.equal(await page.evaluate(() => !document.querySelector('video')?.srcObject), true);

    await page.goto(`${baseURL}/#/tryon-photo?outfit=${encodeURIComponent(JSON.stringify([top.id]))}`);
    await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.width = 640; canvas.height = 800;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ddd'; ctx.fillRect(0, 0, 640, 800);
      ctx.fillStyle = '#333'; ctx.fillRect(180, 50, 280, 700);
      sessionStorage.setItem('shadow.wd.person', canvas.toDataURL('image/jpeg', 0.9));
    });
    await page.reload();
    assert.match(await page.locator('.wd-tryon-main').innerText(), /AI tạo|không chính xác/);
    await page.getByRole('button', { name: 'Mặc thử ngay' }).click();
    const cancel = page.getByRole('button', { name: 'Huỷ' });
    if (await cancel.isVisible()) await cancel.click();
  } finally {
    await browser.close();
  }
}
