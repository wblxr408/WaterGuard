async (page) => {
  const checks = [];
  await page.getByRole("button", { name: "应急工作台", exact: true }).click();
  await page
    .getByRole("combobox", { name: "数据源", exact: true })
    .selectOption("bench");
  await page.locator("#benchmark-case").waitFor();
  const options = await page
    .locator("#benchmark-case option")
    .evaluateAll((nodes) =>
      nodes.map((n) => ({ value: n.value, label: n.textContent })),
    );
  for (const option of options) {
    await page.locator("#benchmark-case").selectOption(option.value);
    await page.locator(`[data-simulation-id="${option.value}"]`).waitFor();
    if (await page.locator(".error-state").count())
      throw new Error(`工况加载失败 ${option.label}`);
    if ((await page.locator(".mode-badge").textContent()) !== "BENCHMARK")
      throw new Error("模式漂移");
    checks.push(option.label);
  }
  await page
    .getByRole("combobox", { name: "数据源", exact: true })
    .selectOption("pair");
  await page
    .locator(".metric strong")
    .first()
    .filter({ hasText: "7.70" })
    .waitFor();
  const pattern = "**/ab/api/networks";
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    const headers = { ...response.headers() };
    delete headers["x-waterguard-mode"];
    await route.fulfill({ response, headers });
  });
  await page.getByRole("button", { name: "刷新数据", exact: true }).click();
  await page
    .locator(".error-state")
    .filter({ hasText: "响应头为 缺失" })
    .waitFor();
  if (!(await page.locator(".error-state").textContent()).includes("缺失"))
    throw new Error("未拒绝缺失模式头");
  if (await page.locator(".metric-strip").count())
    throw new Error("错误时保留了旧指标");
  checks.push("缺失模式头：拒绝且清空旧结果");
  await page.unroute(pattern);
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      headers: { ...response.headers(), "x-waterguard-mode": "fixture" },
    });
  });
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await page
    .locator(".error-state")
    .filter({ hasText: "响应头为 fixture" })
    .waitFor();
  if (
    !(await page.locator(".error-state").textContent()).includes(
      "响应头为 fixture",
    )
  )
    throw new Error("未拒绝混合模式");
  checks.push("不匹配模式头：拒绝 Fixture / Benchmark 混用");
  await page.unroute(pattern);
  await page.route(pattern, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: '{"error":"verification-only"}',
    }),
  );
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await page.locator(".error-state").filter({ hasText: "503" }).waitFor();
  if (!(await page.locator(".error-state").textContent()).includes("503"))
    throw new Error("未展示不可用错误");
  await page.screenshot({
    path: "output/playwright/07-unavailable.png",
    animations: "disabled",
  });
  checks.push("503 不可用：可重试且不虚构数据");
  await page.unroute(pattern);
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await page
    .locator(".metric strong")
    .first()
    .filter({ hasText: "7.70" })
    .waitFor();
  checks.push("重试：恢复真实 A/B");
  await page.setViewportSize({ width: 768, height: 1024 });
  if (
    !(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ))
  )
    throw new Error("平板宽度溢出");
  checks.push("768px 平板无页面溢出");
  await page.setViewportSize({ width: 1600, height: 1150 });
  return { passed: checks.length, checks };
}
