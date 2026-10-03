async (page) => {
  await page.getByRole('combobox', { name: '数据源', exact: true }).selectOption('pair');
  await page.getByRole('button', { name: '应急工作台', exact: true }).click();
  await page.locator('.metric strong').first().filter({ hasText: '7.70' }).waitFor();
  await page.setViewportSize({ width: 1600, height: 1150 });
  const capture = async (path, fullPage = false) => {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.mouse.move(0, 0);
    await page.screenshot({ path: `output/playwright/${path}`, fullPage, animations: 'disabled' });
  };
  await capture('01-workbench.png');
  await capture('01-workbench-full.png', true);
  await page.getByRole('button', { name: '建模与仿真', exact: true }).click();
  await page.locator('.model-grid').waitFor();
  await capture('02-model-studio.png');
  await capture('02-model-studio-full.png', true);
  await page.getByRole('button', { name: '方案对照', exact: true }).click();
  await page.locator('.comparison-layout').waitFor();
  await capture('03-comparison.png');
  await page.getByRole('button', { name: '证据中心', exact: true }).click();
  await page.locator('.evidence-table').waitFor();
  await capture('04-evidence.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '应急工作台', exact: true }).click();
  await capture('05-mobile-workbench.png', true);
  await capture('05-mobile-viewport.png');
  await page.getByRole('button', { name: '建模与仿真', exact: true }).click();
  await capture('06-mobile-models.png', true);
  await capture('06-mobile-viewport.png');
  await page.setViewportSize({ width: 1600, height: 1150 });
  await page.getByRole('button', { name: '应急工作台', exact: true }).click();
  return '已保存新版桌面与手机实际渲染截图';
}
