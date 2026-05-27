import { defineConfig } from 'vite'
import { builtinModules } from 'module'
import path from 'path'

export default defineConfig({
  build: {
    lib: {
      entry: path.resolve(__dirname, 'src/main/main.ts'),
      formats: ['cjs'],
      fileName: () => 'main.js',
    },
    outDir: 'dist/main',
    emptyOutDir: true,
    rollupOptions: {
      // Mark Electron + all Node built-ins (and their `node:` variants) as
      // external so they are not bundled or replaced with browser shims.
      external: [
        'electron',
        ...builtinModules,
        ...builtinModules.map(m => `node:${m}`),
      ],
    },
    target: 'node18',
    minify: false,
  },
})
