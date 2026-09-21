const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 1080,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-header-help-regression', contextIsolation: false,
      preload: path.join(__dirname, 'header-help-ui-preload.cjs'),
    },
  })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    const evaluate = expression => win.webContents.executeJavaScript(expression)
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.body.innerText.includes("联系我")')) break
      await pause()
    }
    assert.equal(await evaluate('document.body.innerText.includes("联系我")'), true)
    assert.equal(await evaluate('document.body.innerText.includes("VX：18667026883")'), false)
    await evaluate('document.querySelector("[aria-label=显示微信联系方式]").click()')
    assert.equal(await evaluate('document.body.innerText.includes("VX：18667026883")'), true)
    await evaluate('document.querySelector("[aria-label=隐藏微信联系方式]").click()')
    assert.equal(await evaluate('document.body.innerText.includes("VX：18667026883")'), false)
    await evaluate('document.querySelector("[aria-label=打开重叠率说明]").click()')
    assert.equal(await evaluate('document.querySelector("[aria-label=打开重叠率说明]").getAttribute("aria-expanded")'), 'true')
    assert.equal(await evaluate('document.querySelector("#overlap-rate-help").innerText.includes("0%") && document.querySelector("#overlap-rate-help").innerText.includes("30%")'), true)
    await evaluate('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))')
    assert.equal(await evaluate('document.querySelector("#overlap-rate-help")'), null)
    assert.equal(await evaluate(`(() => {
      const panel = [...document.querySelectorAll('div')].find(node => node.className.includes('overflow-y-auto'))
      const output = [...document.querySelectorAll('h2')].find(node => node.textContent.trim() === '输出')
      return Boolean(panel && output && output.getBoundingClientRect().bottom <= panel.getBoundingClientRect().bottom)
    })()`), true)
    await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent.trim() === "按原素材时长").click()')
    await pause(600)
    assert.equal(await evaluate('document.body.innerText.includes("14.0–17.0s")'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent === "首段").parentElement.querySelector("input").disabled'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent === "Hook").parentElement.querySelector("input").value'), '1')
    await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent.trim() === "按原素材时长").click()')
    await pause()
    assert.equal(await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent === "Hook").parentElement.querySelector("input").value'), '0.5')
    await pause(500)
    win.webContents.invalidate()
    await pause(200)
    fs.writeFileSync(path.join(__dirname, '../release/header-help-ui-test.png'), (await win.webContents.capturePage()).toPNG())
    console.log('PASS contact, help, full-hook switch disables inputs and restores overlap, output visibility')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
