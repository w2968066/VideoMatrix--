const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1600, height: 1100,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-slider-regression', contextIsolation: false,
      preload: path.join(__dirname, 'slider-preview-preload.cjs'),
    },
  })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    const evaluate = expression => win.webContents.executeJavaScript(expression)
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.querySelectorAll("[role=slider]").length === 2')) break
      await pause()
    }
    assert.equal(await evaluate('document.querySelectorAll("[role=slider]").length'), 2)
    await evaluate(`window.__sliderEvents = []; for (const type of ['pointerdown', 'pointermove', 'pointerup', 'lostpointercapture', 'blur']) window.addEventListener(type, e => { const t = e.target; window.__sliderEvents.push({ type, clientX: e.clientX, offsetX: e.offsetX, target: t.getAttribute?.('role'), width: t.clientWidth, rect: t.getBoundingClientRect?.().toJSON(), buttons: e.buttons }); });`)
    win.webContents.debugger.attach('1.3')
    let zoomScale = 1
    const send = (method, params) => win.webContents.debugger.sendCommand(method, params)
    const box = async selector => {
      const { root } = await send('DOM.getDocument')
      const { nodeId } = await send('DOM.querySelector', { nodeId: root.nodeId, selector })
      const { model } = await send('DOM.getBoxModel', { nodeId })
      const c = model.content
      // Chromium 120 reports DOM boxes before CSS zoom, but input uses viewport pixels.
      return { left: c[0] * zoomScale, top: c[1] * zoomScale, right: c[2] * zoomScale, bottom: c[5] * zoomScale }
    }
    const mouse = async (type, x, y, pressed = false) => {
      await send('Input.dispatchMouseEvent', {
        type, x, y, button: type === 'mouseMoved' && !pressed ? 'none' : 'left',
        buttons: pressed ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1,
      })
      await pause()
    }
    const preview = () => evaluate('!!document.querySelector("[data-subtitle-preview]")')
    for (const zoom of [1, 1.15, 1.5]) {
      zoomScale = zoom
      await evaluate(`document.documentElement.style.setProperty('--ui-scale', '${zoom}')`)
      await pause()
      for (const label of ['字幕垂直位置', '字幕字号']) {
        const selector = `[role="slider"][aria-label="${label}"]`
        const b = await box(selector)
        const y = (b.top + b.bottom) / 2
        await mouse('mouseMoved', b.left + 10, y)
        assert.equal(await preview(), false, 'hover must not open preview')
        let initialText
        for (const [i, ratio] of [0.237, 0.5, 0.683, 0.921].entries()) {
          const x = b.left + (b.right - b.left) * ratio
          await mouse(i === 0 ? 'mousePressed' : 'mouseMoved', x, y, true)
          const t = await box(`${selector} [data-slider-thumb]`)
          const error = Math.abs((t.left + t.right) / 2 - x)
          if (error >= 2) console.log(JSON.stringify({ box: b, thumb: t, x, events: await evaluate('window.__sliderEvents.slice(-8)') }))
          assert.ok(error < 2, `${label} zoom=${zoom} ratio=${ratio}: thumb error ${error}px`)
          assert.equal(await preview(), true, 'press/drag must show preview')
          assert.equal(await evaluate('document.querySelector("[data-preview-text]").textContent'), '这是十个字的字幕示例', 'preview must show all ten Chinese characters')
          assert.equal(await evaluate(`(() => {
            const p = document.querySelector('[data-subtitle-preview]');
            const r = p.getBoundingClientRect();
            return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight
              && getComputedStyle(p).pointerEvents === 'none';
          })()`), true, 'preview must stay in viewport and never intercept pointer events')
          const text = await evaluate('document.querySelector("[data-subtitle-preview]").textContent')
          if (i === 0) initialText = text
          else assert.notEqual(text, initialText, 'preview must update while dragging')
          console.log(`PASS ${label} zoom=${zoom} ratio=${ratio} error=${error.toFixed(2)}px`)
          if (zoom === 1.15 && label === '字幕垂直位置' && i === 1) {
            win.webContents.invalidate()
            await pause(200)
            fs.writeFileSync(path.join(__dirname, '../release/subtitle-preview-test.png'), (await win.webContents.capturePage()).toPNG())
          }
        }
        await mouse('mouseReleased', b.right + 25, y + 50)
        assert.equal(await preview(), false, 'release outside track must close preview')
      }
    }
    const b = await box('[aria-label="字幕字号"]')
    await mouse('mousePressed', (b.left + b.right) / 2, (b.top + b.bottom) / 2, true)
    await evaluate('window.dispatchEvent(new Event("blur"))')
    assert.equal(await preview(), false, 'blur must close preview')
    console.log('PASS preview lifecycle: hover, press, drag, outside release, blur')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
