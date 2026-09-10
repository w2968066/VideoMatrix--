const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-grouped-body-regression', contextIsolation: false,
      preload: path.join(__dirname, 'grouped-body-ui-preload.cjs'),
    },
  })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    const evaluate = expression => win.webContents.executeJavaScript(expression)
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.body.innerText.includes("各组内随机抽取")')) break
      await pause()
    }
    assert.equal(await evaluate('document.body.innerText.includes("各组内随机抽取")'), true)
    assert.equal(await evaluate('document.querySelectorAll("[role=switch]").length >= 4'), true)
    assert.equal(await evaluate('document.body.innerText.includes("共 4 段 · 9.0s")'), true)
    assert.equal(await evaluate('document.body.innerText.includes("随机首帧")'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("label")].some(node => ["后段", "片段"].includes(node.textContent.trim()))'), false)
    await evaluate(`(() => {
      const input = document.querySelector('input[title="片段数（至少 1）"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, '3'); input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`)
    await pause()
    assert.equal(await evaluate('document.body.innerText.includes("共 5 段 · 11.0s")'), true)
    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "插入一帧").click()')
    await pause()
    assert.equal(await evaluate('document.body.innerText.includes("共 5 段 · 11.033s")'), true)
    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "普通").click()')
    await pause()
    assert.equal(await evaluate('Boolean(document.querySelector("input[value=\\"normal-body\\"]"))'), true)
    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "分组").click()')
    await pause()
    assert.equal(await evaluate('document.body.innerText.includes("共 5 段 · 11.033s")'), true)
    assert.equal(await evaluate(`(() => {
      const panel = [...document.querySelectorAll('div')].find(node => node.className.includes('overflow-y-auto'))
      return Boolean(panel && panel.scrollHeight > panel.clientHeight)
    })()`), true)
    win.webContents.invalidate()
    await pause(200)
    fs.writeFileSync(path.join(__dirname, '../release/grouped-body-ui-test.png'), (await win.webContents.capturePage()).toPNG())
    console.log('PASS grouped Body UI: totals update, normal settings persist, stale controls hidden, random-cover hint, scrollable 1280x900 layout')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
