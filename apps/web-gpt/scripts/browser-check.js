// 通过 playwright-cli run-code 执行；非 @playwright/test 测试套件。
async (page) => {
  const results = [];
  const check = (condition, name) => {
    if (!condition) throw new Error(name);
    results.push(name);
  };
  const visible = async (text) =>
    page
      .locator(".metric strong")
      .filter({ hasText: text })
      .first()
      .isVisible();
  await page.setViewportSize({ width: 1600, height: 1150 });
  await page.getByRole("button", { name: "应急工作台", exact: true }).click();
  await page.locator(".metric-strip").waitFor();
  check(await visible("7.70"), "A/B 真实方案 B 缺供值");
  check(await visible("2.63"), "方案 B 最低压力未伪造达标");
  check(
    (await page.locator(".mode-badge").textContent()) === "BENCHMARK",
    "严格 Benchmark 模式标记",
  );
  await page
    .getByRole("button", { name: "方案 A · 不处置", exact: true })
    .click();
  check(await visible("187.31"), "切换方案 A，指标联动");
  check(await visible("-24.47"), "方案 A 保留负压");
  await page
    .getByRole("button", { name: "方案 B · 启动水泵", exact: true })
    .click();
  await page.getByRole("slider", { name: "仿真时间轴" }).fill("8");
  check(
    (await page.locator(".time-now strong").textContent()) === "02:00",
    "时间轴切换真实帧",
  );
  await page.getByRole("button", { name: "管段 123", exact: true }).click();
  check(
    (await page.locator(".entity-title h2").textContent()).includes("123"),
    "管段选择与详情联动",
  );
  check(
    (await page.locator(".entity-details").textContent()).includes("开启"),
    "故障结束后恢复真实管段状态",
  );
  await page
    .getByRole("button", { name: "定位节点 15 01:00", exact: true })
    .click();
  check(
    (await page.locator(".time-now strong").textContent()) === "01:00",
    "违规反馈定位真实时段",
  );
  check(
    (await page.locator(".entity-title h2").textContent()).includes("15"),
    "违规反馈定位真实节点",
  );
  await page.getByRole("button", { name: "建模与仿真", exact: true }).click();
  check(
    (await page.locator(".model-demo").count()) === 2,
    "两个 Blender 预留演示",
  );
  const pump = page.locator(".model-demo").first();
  const before = await pump
    .locator("svg[role=img] polygon")
    .first()
    .getAttribute("points");
  await page.getByRole("slider", { name: "泵站设备视角" }).fill("25");
  const after = await pump
    .locator("svg[role=img] polygon")
    .first()
    .getAttribute("points");
  check(before !== after, "模型视角实际改变几何投影");
  await pump.getByRole("button", { name: "分解查看", exact: true }).click();
  check(
    await pump.getByRole("button", { name: "还原结构" }).isVisible(),
    "模型分解交互",
  );
  await page.getByRole("button", { name: "重置泵站设备", exact: true }).click();
  await page.getByRole("button", { name: "查看实体映射", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  check(
    (await page.locator("dialog pre").textContent()).includes("compiled_links"),
    "真实实体映射可查",
  );
  await page.keyboard.press("Escape");
  check((await page.getByRole("dialog").count()) === 0, "详情 Esc 关闭");
  await page.screenshot({
    path: "output/playwright/02-model-studio.png",
    fullPage: false,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "证据中心", exact: true }).click();
  await page
    .getByRole("textbox", { name: "搜索证据文件" })
    .fill("physical-checks");
  check(
    (await page.locator(".evidence-table tbody tr").count()) === 1,
    "证据搜索筛选",
  );
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  check(
    (await page.locator("dialog pre").textContent()).includes(
      "mass_balance_max_abs_m3s",
    ),
    "真实物理检查可查",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "搜索证据文件" }).fill("");
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载证据索引", exact: true }).click();
  const file = await downloadEvent;
  check(
    file.suggestedFilename().endsWith("-manifest.json"),
    "证据索引真实下载",
  );
  await file.saveAs("output/playwright/evidence-download.json");
  await page.screenshot({
    path: "output/playwright/04-evidence.png",
    fullPage: false,
    animations: "disabled",
  });
  await page
    .getByRole("combobox", { name: "数据源", exact: true })
    .selectOption("fixture");
  await page.locator(".mode-badge.fixture").waitFor();
  await page.getByRole("button", { name: "应急工作台", exact: true }).click();
  await page.getByText("回放完成", { exact: true }).waitFor();
  check(await visible("24.00"), "Fixture 数据与 Benchmark 隔离");
  check(
    (await page.locator(".event-list button").count()) === 8,
    "Fixture 命名 SSE 事件完整且去重",
  );
  check(
    (await page.locator(".inspector-verdict").textContent()).includes(
      "不可推荐",
    ),
    "Fixture 名义达标仍不伪造推荐",
  );
  await page
    .getByRole("combobox", { name: "数据源", exact: true })
    .selectOption("bench");
  await page.locator("#benchmark-case").waitFor();
  const options = await page
    .locator("#benchmark-case option")
    .evaluateAll((nodes) =>
      nodes.map((n) => ({ value: n.value, text: n.textContent })),
    );
  check(options.length === 22, "完整真实场景库 22 工况");
  const leak = options.find(
    (o) =>
      o.text.includes("EPA-NET3") &&
      o.text.includes("WNTR") &&
      o.text.includes("管段泄漏"),
  );
  await page.locator("#benchmark-case").selectOption(leak.value);
  await page
    .locator(".task-strip strong")
    .filter({ hasText: "管段泄漏" })
    .waitFor();
  check(
    (await page.locator(".mode-badge").textContent()) === "BENCHMARK",
    "泄漏场景模式未漂移",
  );
  await page
    .getByRole("combobox", { name: "数据源", exact: true })
    .selectOption("pair");
  await page
    .locator(".metric strong")
    .first()
    .filter({ hasText: "7.70" })
    .waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  check(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "手机工作台无页面横向溢出",
  );
  await page.screenshot({
    path: "output/playwright/05-mobile-workbench.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "建模与仿真", exact: true }).click();
  check(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "手机建模页无横向溢出",
  );
  await page.screenshot({
    path: "output/playwright/06-mobile-models.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1600, height: 1150 });
  await page.getByRole("button", { name: "方案对照", exact: true }).click();
  await page.screenshot({
    path: "output/playwright/03-comparison.png",
    fullPage: false,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "应急工作台", exact: true }).click();
  await page.screenshot({
    path: "output/playwright/01-workbench.png",
    fullPage: false,
    animations: "disabled",
  });
  return { passed: results.length, results };
}
