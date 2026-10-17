const { chromium, webkit, firefox } = require("./pw/index.js");
(async () => {
  for (const [name, type] of [["chromium", chromium], ["webkit", webkit], ["firefox", firefox]]) {
    const b = await type.launch();
    const page = await b.newPage({ viewport: { width: 1500, height: 860 } });
    for (const mode of ["", "#six", "#virt", "#six-virt"]) {
      await page.goto("file:///w/research_proto/list_proto.html" + mode);
      await page.reload(); await page.evaluate(() => document.fonts.ready);
      const r1 = await page.evaluate(() => window.bench()); await page.reload();
      const r = await page.evaluate(() => window.bench());
      console.log(name, mode || "#all21", JSON.stringify(r));
      if (name === "chromium" && mode === "#six") {
        await page.reload(); await page.evaluate(() => document.fonts.ready);
        await page.click("#body tr:nth-child(5) td");
        console.log("PNG proto-six-dark " + (await page.screenshot()).toString("base64"));
        await page.click("#theme"); console.log("PNG proto-six-light " + (await page.screenshot()).toString("base64"));
      }
    }
    await b.close();
  }
})();
