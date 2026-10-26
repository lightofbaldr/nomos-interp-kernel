import {sveltekit} from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import {defineConfig} from 'vite';
export default defineConfig({tsconfig:'./tsconfig.json',plugins:[tailwindcss(),sveltekit()]});
