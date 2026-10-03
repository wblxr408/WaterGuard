// Layout checks for the compact workbench and the shared luminous theme.
async (page) => {
  const results = [];
  const check = (condition, message) => {
    if (!condition) throw new Error(message);
    results.push(message);
  };
  await page.getByRole('combobox', { name: '数据源', exact: true }).selectOption('pair');
  for (const width of [1600, 1440, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.getByRole('button', { name: '应急工作台', exact: true }).click();
    await page.getByRole('button', { name: '重置视图', exact: true }).click();
    check(await page.locator('.metric').count() === 3, `${width}: only three essential metrics`);
    const bounds = await page.locator('.network-canvas').evaluate(canvas => {
      const area = canvas.getBoundingClientRect();
      const nodes = Array.from(canvas.querySelectorAll('.map-entity[aria-label^="节点"]'));
      return nodes.every(node => {
        const r = node.getBoundingClientRect();
        return r.top >= area.top - 1 && r.bottom <= area.bottom + 1 && r.left >= area.left - 1 && r.right <= area.right + 1;
      });
    });
    check(bounds, `${width}: default network nodes fit the canvas`);
    for (const name of ['应急工作台', '方案对照', '建模与仿真', '证据中心']) {
      await page.getByRole('button', { name, exact: true }).click();
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width} ${name}: no document overflow`);
      check(await page.locator('img').evaluateAll(async images => {
        await Promise.all(images.map(image => image.decode().catch(() => {})));
        return images.every(image => image.complete && image.naturalWidth > 0);
      }), `${width} ${name}: image assets loaded`);
    }
  }
  await page.setViewportSize({ width: 1600, height: 1150 });
  await page.getByRole('button', { name: '应急工作台', exact: true }).click();
  return { passed: results.length, results };
}
