import {sveltekit} from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';
export default defineConfig({tsconfig:'./tsconfig.json',define:{__GLIMMER_APP_ROOT__:JSON.stringify(fileURLToPath(new URL('.',import.meta.url)))},plugins:[tailwindcss(),sveltekit()]});
